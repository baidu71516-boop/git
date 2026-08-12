"""PostgreSQL 16 concurrency gates for Phase 2 bulk file uploads.

The module only accepts an explicitly named disposable test database.  Each test
creates and destroys its own PostgreSQL schema so no development or production
objects are touched.
"""

import asyncio
import os
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
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.auth.service import AuthContext
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.enums import (
    CollectionJobStatus,
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
    ImportRow,
    StoredImportFile,
)
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.service import ImportService
from backend_core.imports.storage import LocalStorageAdapter
from sqlalchemy import func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlalchemy.sql.elements import ColumnElement


def _gated_test_database_url() -> URL:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("TEST_DATABASE_URL is not set", allow_module_level=True)
    try:
        url = make_url(raw_url)
    except ArgumentError as error:
        pytest.fail(f"TEST_DATABASE_URL is invalid: {error}", pytrace=False)
    database_name = (url.database or "").lower()
    if url.get_backend_name() != "postgresql" or "phase1b_test" not in database_name:
        pytest.fail(
            "TEST_DATABASE_URL must be PostgreSQL and its database name must contain "
            "'phase1b_test'",
            pytrace=False,
        )
    return url.set(drivername="postgresql+psycopg")


TEST_DATABASE_URL = _gated_test_database_url()


@dataclass(frozen=True)
class SeededBatch:
    department_id: UUID
    operator_id: UUID
    auth_session_id: UUID
    collection_job_id: UUID
    import_job_ids: tuple[UUID, ...]


@asynccontextmanager
async def _isolated_postgres() -> (
    AsyncIterator[tuple[async_sessionmaker[AsyncSession], LocalStorageAdapter]]
):
    schema_name = f"phase2_bulk_upload_{uuid4().hex}"
    admin_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    test_engine: AsyncEngine | None = None
    schema_created = False
    try:
        async with admin_engine.begin() as connection:
            version_number = int(await connection.scalar(text("SHOW server_version_num")))
            assert version_number // 10_000 == 16
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
        with TemporaryDirectory(prefix="phase2-bulk-upload-postgres-") as storage_directory:
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


async def _seed_batches(
    factory: async_sessionmaker[AsyncSession], *, job_count: int
) -> SeededBatch:
    async with factory() as session:
        department = Department(
            name=f"Phase 2 PostgreSQL upload fixture {uuid4().hex}",
            password_hash="not-used-by-integration-test",
            status=DepartmentStatus.ACTIVE,
            session_days=30,
        )
        session.add(department)
        await session.flush()
        operator = Operator(
            department_id=department.id,
            name="Phase 2 upload operator",
            role=Role.OPERATOR,
            status=OperatorStatus.ACTIVE,
        )
        session.add(operator)
        session.add(DepartmentPermission(department_id=department.id, role=Role.OPERATOR))
        await session.flush()
        auth_session = AuthSession(
            department_id=department.id,
            operator_id=operator.id,
            token_hash=uuid4().hex + uuid4().hex,
            csrf_token_hash=uuid4().hex + uuid4().hex,
            ip="127.0.0.1",
            user_agent="phase2-postgres-concurrency-test",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            revoked_at=None,
        )
        session.add(auth_session)
        collection = CollectionJob(
            name="Phase 2 PostgreSQL upload collection",
            industry="测试",
            purpose="验证上传幂等并发",
            target_action="导入",
            target_count=max(job_count, 1),
            department_id=department.id,
            owner_operator_id=operator.id,
            source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
            status=CollectionJobStatus.ACTIVE,
        )
        session.add(collection)
        await session.flush()
        jobs = [
            ImportJob(
                collection_job_id=collection.id,
                department_id=department.id,
                operator_id=operator.id,
                source_type=collection.source_type,
                status=ImportJobStatus.DRAFT,
                preview_revision=0,
            )
            for _ in range(job_count)
        ]
        session.add_all(jobs)
        await session.commit()
        return SeededBatch(
            department_id=department.id,
            operator_id=operator.id,
            auth_session_id=auth_session.id,
            collection_job_id=collection.id,
            import_job_ids=tuple(job.id for job in jobs),
        )


async def _context(session: AsyncSession, seeded: SeededBatch) -> AuthContext:
    department = await session.get(Department, seeded.department_id)
    operator = await session.get(Operator, seeded.operator_id)
    auth_session = await session.get(AuthSession, seeded.auth_session_id)
    assert department is not None
    assert operator is not None
    assert auth_session is not None
    return AuthContext(
        department=department,
        operator=operator,
        role=Role.OPERATOR,
        auth_session=auth_session,
    )


def _service(session: AsyncSession, storage: LocalStorageAdapter) -> ImportService:
    return ImportService(
        session,
        storage,
        parser_limits=ParserLimits(max_rows=100, max_columns=100, max_cells=10_000),
        max_file_bytes=25 * 1024 * 1024,
        retention_days=30,
    )


async def _chunks(content: bytes) -> AsyncIterator[bytes]:
    midpoint = len(content) // 2
    yield content[:midpoint]
    yield content[midpoint:]


async def _upload(
    factory: async_sessionmaker[AsyncSession],
    storage: LocalStorageAdapter,
    seeded: SeededBatch,
    *,
    job_id: UUID,
    client_file_id: str,
    content: bytes,
) -> object:
    async with factory() as session:
        service = _service(session, storage)
        context = await _context(session, seeded)
        return await service.upload_import_job_file(
            context,
            import_job_id=job_id,
            client_file_id=client_file_id,
            filename=f"{client_file_id}.csv",
            declared_mime="text/csv",
            chunks=_chunks(content),
            source_acquired_at=None,
            ip="127.0.0.1",
            user_agent="phase2-postgres-concurrency-test",
        )


async def _upload_concurrently(
    factory: async_sessionmaker[AsyncSession],
    storage: LocalStorageAdapter,
    seeded: SeededBatch,
    requests: list[tuple[UUID, str, bytes]],
) -> list[object | BaseException]:
    barrier = asyncio.Barrier(len(requests) + 1)

    async def upload(request: tuple[UUID, str, bytes]) -> object:
        await barrier.wait()
        job_id, client_file_id, content = request
        return await _upload(
            factory,
            storage,
            seeded,
            job_id=job_id,
            client_file_id=client_file_id,
            content=content,
        )

    tasks = [asyncio.create_task(upload(request)) for request in requests]
    await barrier.wait()
    return list(await asyncio.gather(*tasks, return_exceptions=True))


async def _count(
    session: AsyncSession,
    model: type[object],
    *criteria: ColumnElement[bool],
) -> int:
    statement = select(func.count()).select_from(model)
    if criteria:
        statement = statement.where(*criteria)
    return int(await session.scalar(statement) or 0)


def _assert_successes(results: list[object | BaseException]) -> None:
    errors = [result for result in results if isinstance(result, BaseException)]
    assert errors == []


def _stored_objects(storage: LocalStorageAdapter) -> list[Path]:
    return sorted(path for path in storage.root.iterdir() if path.is_file())


def test_concurrent_same_client_same_sha_creates_one_occurrence_and_alias() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as (factory, storage):
            seeded = await _seed_batches(factory, job_count=1)
            job_id = seeded.import_job_ids[0]
            content = b"name,followers\nAlpha,100\n"

            results = await _upload_concurrently(
                factory,
                storage,
                seeded,
                [(job_id, "A", content), (job_id, "A", content)],
            )

            _assert_successes(results)
            async with factory() as session:
                assert (
                    await _count(
                        session,
                        ImportJobFile,
                        ImportJobFile.import_job_id == job_id,
                    )
                    == 1
                )
                assert (
                    await _count(
                        session,
                        ImportJobFileClientId,
                        ImportJobFileClientId.import_job_id == job_id,
                    )
                    == 1
                )
                assert await _count(session, StoredImportFile) == 1
                assert await _count(session, ImportRow) == 0
                assert (
                    await _count(
                        session,
                        AuditLog,
                        AuditLog.action == AuditAction.IMPORT_FILE_UPLOADED,
                    )
                    == 1
                )
                assert len(_stored_objects(storage)) == 1

    asyncio.run(scenario())


def test_concurrent_distinct_clients_same_sha_share_occurrence_and_keep_both_aliases() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as (factory, storage):
            seeded = await _seed_batches(factory, job_count=1)
            job_id = seeded.import_job_ids[0]
            content = b"name,followers\nAlpha,100\n"

            results = await _upload_concurrently(
                factory,
                storage,
                seeded,
                [(job_id, "A", content), (job_id, "B", content)],
            )

            _assert_successes(results)
            async with factory() as session:
                occurrences = list(
                    await session.scalars(
                        select(ImportJobFile).where(ImportJobFile.import_job_id == job_id)
                    )
                )
                aliases = list(
                    await session.scalars(
                        select(ImportJobFileClientId)
                        .where(ImportJobFileClientId.import_job_id == job_id)
                        .order_by(ImportJobFileClientId.client_file_id)
                    )
                )
                assert len(occurrences) == 1
                assert [alias.client_file_id for alias in aliases] == ["A", "B"]
                assert {alias.import_job_file_id for alias in aliases} == {occurrences[0].id}
                assert await _count(session, StoredImportFile) == 1
                assert await _count(session, ImportRow) == 0
                assert (
                    await _count(
                        session,
                        AuditLog,
                        AuditLog.action == AuditAction.IMPORT_FILE_UPLOADED,
                    )
                    == 1
                )
                assert len(_stored_objects(storage)) == 1

    asyncio.run(scenario())


def test_concurrent_same_client_different_sha_has_one_success_and_deterministic_conflict() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as (factory, storage):
            seeded = await _seed_batches(factory, job_count=1)
            job_id = seeded.import_job_ids[0]

            results = await _upload_concurrently(
                factory,
                storage,
                seeded,
                [
                    (job_id, "A", b"name,followers\nAlpha,100\n"),
                    (job_id, "A", b"name,followers\nBeta,200\n"),
                ],
            )

            successes = [result for result in results if not isinstance(result, BaseException)]
            errors = [result for result in results if isinstance(result, BaseException)]
            assert len(successes) == 1
            assert len(errors) == 1
            assert isinstance(errors[0], ImportDomainError)
            assert errors[0].code == "IDEMPOTENCY_CONFLICT"
            assert errors[0].status_code == 409
            async with factory() as session:
                assert (
                    await _count(
                        session,
                        ImportJobFile,
                        ImportJobFile.import_job_id == job_id,
                    )
                    == 1
                )
                assert (
                    await _count(
                        session,
                        ImportJobFileClientId,
                        ImportJobFileClientId.import_job_id == job_id,
                    )
                    == 1
                )
                assert await _count(session, StoredImportFile) == 1
                assert await _count(session, ImportRow) == 0
                assert len(_stored_objects(storage)) == 1

    asyncio.run(scenario())


def test_concurrent_distinct_files_receive_unique_stable_positions() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as (factory, storage):
            seeded = await _seed_batches(factory, job_count=1)
            job_id = seeded.import_job_ids[0]

            results = await _upload_concurrently(
                factory,
                storage,
                seeded,
                [
                    (job_id, "A", b"name,followers\nAlpha,100\n"),
                    (job_id, "B", b"name,followers\nBeta,200\n"),
                ],
            )

            _assert_successes(results)
            async with factory() as session:
                positions = list(
                    await session.scalars(
                        select(ImportJobFile.position)
                        .where(ImportJobFile.import_job_id == job_id)
                        .order_by(ImportJobFile.position)
                    )
                )
                assert positions == [1, 2]
                assert (
                    await _count(
                        session,
                        ImportJobFileClientId,
                        ImportJobFileClientId.import_job_id == job_id,
                    )
                    == 2
                )

    asyncio.run(scenario())


def test_cross_job_same_sha_reuses_blob_but_creates_independent_occurrences() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as (factory, storage):
            seeded = await _seed_batches(factory, job_count=2)
            first_job_id, second_job_id = seeded.import_job_ids
            content = b"name,followers\nAlpha,100\n"

            results = await _upload_concurrently(
                factory,
                storage,
                seeded,
                [
                    (first_job_id, "A", content),
                    (second_job_id, "A", content),
                ],
            )

            _assert_successes(results)
            async with factory() as session:
                occurrences = list(
                    await session.scalars(
                        select(ImportJobFile).order_by(ImportJobFile.import_job_id)
                    )
                )
                aliases = list(await session.scalars(select(ImportJobFileClientId)))
                assert len(occurrences) == 2
                assert {file.import_job_id for file in occurrences} == {
                    first_job_id,
                    second_job_id,
                }
                assert len({file.stored_file_id for file in occurrences}) == 1
                assert {file.source_acquired_at_origin for file in occurrences} == {
                    SourceAcquiredAtOrigin.SERVER_DEFAULT
                }
                assert sorted(
                    file.source_acquired_at_confirmation_required for file in occurrences
                ) == [False, True]
                assert len(aliases) == 2
                assert {alias.import_job_id for alias in aliases} == {
                    first_job_id,
                    second_job_id,
                }
                assert await _count(session, StoredImportFile) == 1
                assert await _count(session, ImportRow) == 0
                assert len(_stored_objects(storage)) == 1

    asyncio.run(scenario())
