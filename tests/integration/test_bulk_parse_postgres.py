"""PostgreSQL 16 gates for Phase 2 per-file parse and batch dedup staging.

The module only accepts an explicitly named disposable test database. Each test
creates and drops an isolated PostgreSQL schema; development and production
objects are never touched.
"""

import asyncio
import csv
import io
import os
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID, uuid4

import pytest
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, Operator
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.batch_processor import BatchImportProcessor
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportMatchType,
    ImportRowAction,
    ImportSourceType,
    SourceAcquiredAtOrigin,
    StoredFileType,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportRow,
    StoredImportFile,
)
from backend_core.imports.parsers import ParserLimits
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

GENERIC_MAPPING = {
    "name": "nickname",
    "account_id": "platform_account_id",
    "external_id": "external_source_id",
    "profile_url": "profile_url",
    "updated_at": "source_updated_at",
    "followers": "followers_count",
    "email": "email",
}
CSV_HEADERS = (*GENERIC_MAPPING, "fixture_nonce")


@dataclass(frozen=True, slots=True)
class SeededBatch:
    job_id: UUID
    department_id: UUID
    operator_id: UUID
    files: tuple[ImportJobFile, ...]


@asynccontextmanager
async def _isolated_postgres() -> (
    AsyncIterator[tuple[async_sessionmaker[AsyncSession], LocalStorageAdapter]]
):
    schema_name = f"phase2_bulk_parse_{uuid4().hex}"
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
        with TemporaryDirectory(prefix="phase2-bulk-parse-postgres-") as storage_directory:
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


async def _one_chunk(content: bytes) -> AsyncIterator[bytes]:
    yield content


def _csv_bytes(rows: Iterable[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_HEADERS)
    writer.writeheader()
    for row in rows:
        writer.writerow({header: row.get(header, "") for header in CSV_HEADERS})
    return stream.getvalue().encode()


def _row(
    account_id: str,
    *,
    name: str | None = None,
    external_id: str = "",
    profile_url: str = "",
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


async def _store_csv(
    session: AsyncSession,
    storage: LocalStorageAdapter,
    rows: Iterable[dict[str, str]],
) -> StoredImportFile:
    content = _csv_bytes(rows)
    stored = await storage.store(_one_chunk(content), suffix=".csv", max_bytes=25 * 1024 * 1024)
    model = StoredImportFile(
        sha256=stored.sha256,
        storage_key=stored.storage_key,
        size=stored.size,
        detected_type=StoredFileType.CSV,
        detected_mime="text/csv",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    session.add(model)
    await session.flush()
    return model


async def _seed_batch(
    factory: async_sessionmaker[AsyncSession],
    storage: LocalStorageAdapter,
    file_rows: Iterable[Iterable[dict[str, str]]],
    *,
    statuses: Iterable[ImportJobFileStatus] | None = None,
) -> SeededBatch:
    rows_by_file = [list(rows) for rows in file_rows]
    file_statuses = list(statuses or [ImportJobFileStatus.PARSING] * len(rows_by_file))
    assert len(file_statuses) == len(rows_by_file)
    async with factory() as session:
        department = Department(
            name=f"Phase 2 parse fixture {uuid4().hex}",
            password_hash="not-used-by-integration-test",
            status=DepartmentStatus.ACTIVE,
            session_days=30,
        )
        session.add(department)
        await session.flush()
        operator = Operator(
            department_id=department.id,
            name="Phase 2 parse operator",
            role=Role.OPERATOR,
            status=OperatorStatus.ACTIVE,
        )
        session.add(operator)
        await session.flush()
        collection = CollectionJob(
            name="Phase 2 PostgreSQL parse collection",
            industry="test",
            purpose="verify per-file parse and batch dedup",
            target_action="import",
            target_count=max(sum(len(rows) for rows in rows_by_file), 1),
            department_id=department.id,
            owner_operator_id=operator.id,
            source_type=ImportSourceType.GENERIC_CSV,
            status=CollectionJobStatus.ACTIVE,
        )
        session.add(collection)
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
        files: list[ImportJobFile] = []
        for position, (rows, status) in enumerate(
            zip(rows_by_file, file_statuses, strict=True), start=1
        ):
            stored_file = await _store_csv(session, storage, rows)
            task_id = f"parse-{job.id}-{position}"
            occurrence = ImportJobFile(
                import_job_id=job.id,
                stored_file_id=stored_file.id,
                position=position,
                original_filename=f"sanitized-{position}.csv",
                declared_mime="text/csv",
                status=status,
                source_acquired_at=datetime.now(UTC),
                source_acquired_at_origin=SourceAcquiredAtOrigin.SERVER_DEFAULT,
                source_acquired_at_confirmation_required=True,
                field_mapping=dict(GENERIC_MAPPING),
                parse_task_id=None if status is ImportJobFileStatus.EXCLUDED else task_id,
                parse_attempts=0 if status is ImportJobFileStatus.EXCLUDED else 1,
                excluded_at=datetime.now(UTC) if status is ImportJobFileStatus.EXCLUDED else None,
            )
            session.add(occurrence)
            files.append(occurrence)
        await session.commit()
        return SeededBatch(
            job_id=job.id,
            department_id=department.id,
            operator_id=operator.id,
            files=tuple(files),
        )


def _processor(session: AsyncSession, storage: LocalStorageAdapter) -> BatchImportProcessor:
    return BatchImportProcessor(
        session,
        storage,
        parser_limits=ParserLimits(max_rows=10_000, max_columns=100, max_cells=1_000_000),
    )


async def _parse(
    factory: async_sessionmaker[AsyncSession],
    storage: LocalStorageAdapter,
    *,
    job_id: UUID,
    file_id: UUID,
    task_id: str,
) -> dict[str, Any]:
    async with factory() as session:
        return await _processor(session, storage).parse_file(job_id, file_id, task_id)


async def _rows(session: AsyncSession, job_id: UUID) -> list[ImportRow]:
    return list(
        await session.scalars(
            select(ImportRow)
            .join(ImportJobFile, ImportJobFile.id == ImportRow.import_job_file_id)
            .where(ImportRow.import_job_id == job_id)
            .order_by(ImportJobFile.position, ImportRow.row_number, ImportRow.id)
        )
    )


def _warning_codes(row: ImportRow) -> set[str]:
    return {str(warning.get("code")) for warning in row.warnings}


def _logical_snapshot(rows: Iterable[ImportRow], positions: dict[UUID, int]) -> list[object]:
    return [
        (
            positions[row.import_job_file_id],
            row.row_number,
            row.action.value,
            row.normalized_data,
            tuple(sorted(_warning_codes(row))),
        )
        for row in rows
    ]


async def _requeue_file(session: AsyncSession, file_id: UUID, *, task_id: str) -> ImportJobFile:
    occurrence = await session.get(ImportJobFile, file_id)
    assert occurrence is not None
    occurrence.status = ImportJobFileStatus.PARSING
    occurrence.parse_task_id = task_id
    occurrence.parse_attempts += 1
    occurrence.parse_started_at = datetime.now(UTC)
    occurrence.parse_completed_at = None
    occurrence.error_code = None
    occurrence.error_message = None
    await session.commit()
    return occurrence


async def _assert_draft_staging(
    session: AsyncSession, job_id: UUID, *, expected_rows: int
) -> list[ImportRow]:
    job = await session.get(ImportJob, job_id)
    assert job is not None
    assert job.status is ImportJobStatus.DRAFT
    assert job.preview_revision == 0
    assert job.preview_summary is None
    rows = await _rows(session, job_id)
    assert len(rows) == expected_rows
    assert {row.preview_revision for row in rows} == {0}
    assert all(row.committed_at is None and row.committed_action is None for row in rows)
    return rows


async def _business_counts(session: AsyncSession) -> tuple[int, ...]:
    models = (
        Influencer,
        InfluencerPlatformAccount,
        PlatformAccountSourceIdentity,
        InfluencerSourceState,
        InfluencerContact,
        InfluencerCurrentMetrics,
        InfluencerMetricSnapshot,
    )
    counts: list[int] = []
    for model in models:
        counts.append(int(await session.scalar(select(func.count()).select_from(model)) or 0))
    return tuple(counts)


def test_file_aware_rows_are_deterministic_and_excluded_file_is_ignored() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as (factory, storage):
            shared = _row("shared-account", name="Shared", nonce="file-one")
            seeded = await _seed_batch(
                factory,
                storage,
                [
                    [shared],
                    [{**shared, "fixture_nonce": "file-two"}],
                    [{**shared, "fixture_nonce": "excluded"}],
                ],
                statuses=(
                    ImportJobFileStatus.PARSING,
                    ImportJobFileStatus.PARSING,
                    ImportJobFileStatus.EXCLUDED,
                ),
            )
            first, second, excluded = seeded.files

            # Finish the later file first. The final graph must still choose the
            # earliest physical locator once position 1 becomes ready.
            await _parse(
                factory,
                storage,
                job_id=seeded.job_id,
                file_id=second.id,
                task_id=str(second.parse_task_id),
            )
            await _parse(
                factory,
                storage,
                job_id=seeded.job_id,
                file_id=first.id,
                task_id=str(first.parse_task_id),
            )

            async with factory() as session:
                before = await _assert_draft_staging(session, seeded.job_id, expected_rows=2)
                assert [(row.import_job_file_id, row.row_number) for row in before] == [
                    (first.id, 2),
                    (second.id, 2),
                ]
                assert before[0].action is ImportRowAction.CREATE
                assert before[1].action is ImportRowAction.SKIP
                assert "BATCH_DUPLICATE" in _warning_codes(before[1])
                assert before[1].merge_plan == {
                    "batch_duplicate": {
                        "group_id": before[1].merge_plan["batch_duplicate"]["group_id"],
                        "owner_import_row_id": str(before[0].id),
                        "owner_import_job_file_id": str(first.id),
                        "owner_file_position": 1,
                        "owner_row_number": 2,
                        "hard_identity_keys": ["platform:xiaohongshu:account:shared-account"],
                    }
                }
                assert not any(row.import_job_file_id == excluded.id for row in before)
                excluded_model = await session.get(ImportJobFile, excluded.id)
                assert excluded_model is not None
                assert excluded_model.status is ImportJobFileStatus.EXCLUDED
                assert excluded_model.excluded_at is not None
                positions = {item.id: item.position for item in seeded.files}
                initial_snapshot = _logical_snapshot(before, positions)
                initial_ids = [row.id for row in before]
                initial_hashes = [row.plan_hash for row in before]

                # Re-run in the opposite completion order. Revision-zero staging
                # and the graph result must converge without duplicate rows.
                await _requeue_file(session, first.id, task_id="opposite-first")
                await _requeue_file(session, second.id, task_id="opposite-second")

            await _parse(
                factory,
                storage,
                job_id=seeded.job_id,
                file_id=first.id,
                task_id="opposite-first",
            )
            await _parse(
                factory,
                storage,
                job_id=seeded.job_id,
                file_id=second.id,
                task_id="opposite-second",
            )

            async with factory() as session:
                after = await _assert_draft_staging(session, seeded.job_id, expected_rows=2)
                positions = {item.id: item.position for item in seeded.files}
                assert _logical_snapshot(after, positions) == initial_snapshot
                assert [row.id for row in after] == initial_ids
                assert [row.plan_hash for row in after] == initial_hashes
                assert await _business_counts(session) == (0, 0, 0, 0, 0, 0, 0)

    asyncio.run(scenario())


def test_reparse_shrink_removes_stale_rows_and_failed_transaction_preserves_old_stage() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as (factory, storage):
            seeded = await _seed_batch(
                factory,
                storage,
                [[_row("shrink-a"), _row("shrink-b"), _row("shrink-c")]],
            )
            occurrence = seeded.files[0]
            await _parse(
                factory,
                storage,
                job_id=seeded.job_id,
                file_id=occurrence.id,
                task_id=str(occurrence.parse_task_id),
            )

            async with factory() as session:
                original = await _assert_draft_staging(session, seeded.job_id, expected_rows=3)
                original_snapshot = [
                    (row.id, row.row_number, row.plan_hash, row.normalized_data) for row in original
                ]
                replacement = await _store_csv(session, storage, [_row("shrink-a")])
                model = await session.get(ImportJobFile, occurrence.id)
                assert model is not None
                model.stored_file_id = replacement.id
                await _requeue_file(session, model.id, task_id="shrink-blocked")
                await session.execute(
                    text(
                        """
                        CREATE FUNCTION reject_stale_row_delete() RETURNS trigger AS $$
                        BEGIN
                            IF OLD.row_number = 4 THEN
                                RAISE EXCEPTION 'injected reparse rollback';
                            END IF;
                            RETURN OLD;
                        END;
                        $$ LANGUAGE plpgsql
                        """
                    )
                )
                await session.execute(
                    text(
                        """
                        CREATE TRIGGER reject_stale_row_delete
                        BEFORE DELETE ON import_rows
                        FOR EACH ROW EXECUTE FUNCTION reject_stale_row_delete()
                        """
                    )
                )
                await session.commit()

            with pytest.raises(ImportDomainError):
                await _parse(
                    factory,
                    storage,
                    job_id=seeded.job_id,
                    file_id=occurrence.id,
                    task_id="shrink-blocked",
                )

            async with factory() as session:
                preserved = await _rows(session, seeded.job_id)
                assert [
                    (row.id, row.row_number, row.plan_hash, row.normalized_data)
                    for row in preserved
                ] == original_snapshot
                failed_job = await session.get(ImportJob, seeded.job_id)
                failed_file = await session.get(ImportJobFile, occurrence.id)
                assert failed_job is not None
                assert failed_job.status is ImportJobStatus.DRAFT
                assert failed_job.preview_revision == 0
                assert failed_job.preview_summary is None
                assert failed_file is not None
                assert failed_file.status is ImportJobFileStatus.FAILED
                await session.execute(text("DROP TRIGGER reject_stale_row_delete ON import_rows"))
                await session.execute(text("DROP FUNCTION reject_stale_row_delete()"))
                await _requeue_file(session, occurrence.id, task_id="shrink-success")

            await _parse(
                factory,
                storage,
                job_id=seeded.job_id,
                file_id=occurrence.id,
                task_id="shrink-success",
            )

            async with factory() as session:
                shrunk = await _assert_draft_staging(session, seeded.job_id, expected_rows=1)
                assert [(row.import_job_file_id, row.row_number) for row in shrunk] == [
                    (occurrence.id, 2)
                ]
                assert shrunk[0].id == original_snapshot[0][0]
                assert await _business_counts(session) == (0, 0, 0, 0, 0, 0, 0)

    asyncio.run(scenario())


def test_two_workers_with_the_same_task_converge_to_one_file_stage() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as (factory, storage):
            input_rows = [_row(f"concurrent-{index}") for index in range(40)]
            seeded = await _seed_batch(factory, storage, [input_rows])
            occurrence = seeded.files[0]
            task_id = str(occurrence.parse_task_id)
            barrier = asyncio.Barrier(3)

            async def worker() -> dict[str, Any]:
                await barrier.wait()
                return await _parse(
                    factory,
                    storage,
                    job_id=seeded.job_id,
                    file_id=occurrence.id,
                    task_id=task_id,
                )

            tasks = [asyncio.create_task(worker()) for _ in range(2)]
            await barrier.wait()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert not [result for result in results if isinstance(result, BaseException)]

            async with factory() as session:
                rows = await _assert_draft_staging(
                    session, seeded.job_id, expected_rows=len(input_rows)
                )
                assert len({(row.import_job_file_id, row.row_number) for row in rows}) == len(
                    input_rows
                )
                file_model = await session.get(ImportJobFile, occurrence.id)
                assert file_model is not None
                assert file_model.status is ImportJobFileStatus.READY
                assert file_model.parse_task_id == task_id
                assert file_model.raw_rows == len(input_rows)
                assert await _business_counts(session) == (0, 0, 0, 0, 0, 0, 0)

    asyncio.run(scenario())


async def _seed_conflicting_database_accounts(
    session: AsyncSession,
    *,
    target_job_id: UUID,
    target_file_id: UUID,
    external_id: str,
) -> tuple[InfluencerPlatformAccount, InfluencerPlatformAccount]:
    first_influencer = Influencer(display_name="Existing A")
    second_influencer = Influencer(display_name="Existing B")
    session.add_all((first_influencer, second_influencer))
    await session.flush()
    first = InfluencerPlatformAccount(
        influencer_id=first_influencer.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id="database-account-a",
        account_name="Existing A",
        source=DataSource.GENERIC,
        is_active=True,
    )
    second = InfluencerPlatformAccount(
        influencer_id=second_influencer.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id="database-account-b",
        account_name="Existing B",
        source=DataSource.GENERIC,
        is_active=True,
    )
    session.add_all((first, second))
    await session.flush()
    target_job = await session.get(ImportJob, target_job_id)
    target_file = await session.get(ImportJobFile, target_file_id)
    assert target_job is not None
    assert target_file is not None
    provenance_job = ImportJob(
        collection_job_id=target_job.collection_job_id,
        department_id=target_job.department_id,
        operator_id=target_job.operator_id,
        source_type=target_job.source_type,
        status=ImportJobStatus.DRAFT,
        preview_revision=0,
    )
    session.add(provenance_job)
    await session.flush()
    provenance_file = ImportJobFile(
        import_job_id=provenance_job.id,
        stored_file_id=target_file.stored_file_id,
        position=1,
        original_filename="sanitized-existing-lineage.csv",
        declared_mime="text/csv",
        status=ImportJobFileStatus.READY,
        source_acquired_at=datetime.now(UTC),
        source_acquired_at_origin=SourceAcquiredAtOrigin.USER_CONFIRMED,
        source_acquired_at_confirmation_required=False,
        field_mapping=dict(GENERIC_MAPPING),
        raw_rows=1,
    )
    session.add(provenance_file)
    await session.flush()
    provenance_row = ImportRow(
        import_job_id=provenance_job.id,
        import_job_file_id=provenance_file.id,
        row_number=2,
        raw_data={"fixture": "existing-lineage"},
        normalized_data=None,
        matched_influencer_id=second_influencer.id,
        matched_platform_account_id=second.id,
        match_type=ImportMatchType.EXTERNAL_SOURCE_ID,
        action=ImportRowAction.NO_CHANGE,
        merge_plan={},
        warnings=[],
        errors=[],
        preview_revision=0,
        plan_hash="0" * 64,
        committed_action=ImportRowAction.NO_CHANGE,
        committed_at=datetime.now(UTC),
    )
    session.add(provenance_row)
    await session.flush()
    session.add(
        PlatformAccountSourceIdentity(
            platform_account_id=second.id,
            platform=Platform.XIAOHONGSHU,
            source=DataSource.GENERIC,
            external_account_id=external_id,
            first_import_job_id=provenance_job.id,
            first_import_row_id=provenance_row.id,
            last_import_job_id=provenance_job.id,
            last_import_row_id=provenance_row.id,
        )
    )
    await session.commit()
    return first, second


def test_database_hard_key_conflict_persists_manual_review_without_business_writes() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as (factory, storage):
            secret_contact = "must-not-leak@example.invalid"
            seeded = await _seed_batch(
                factory,
                storage,
                [
                    [
                        _row(
                            "database-account-a",
                            external_id="database-external-b",
                            email=secret_contact,
                        )
                    ]
                ],
            )
            occurrence = seeded.files[0]
            async with factory() as session:
                first, second = await _seed_conflicting_database_accounts(
                    session,
                    target_job_id=seeded.job_id,
                    target_file_id=occurrence.id,
                    external_id="database-external-b",
                )
                baseline = await _business_counts(session)
                assert baseline == (2, 2, 1, 0, 0, 0, 0)

            await _parse(
                factory,
                storage,
                job_id=seeded.job_id,
                file_id=occurrence.id,
                task_id=str(occurrence.parse_task_id),
            )

            async with factory() as session:
                rows = await _assert_draft_staging(session, seeded.job_id, expected_rows=1)
                row = rows[0]
                assert row.action is ImportRowAction.MANUAL_REVIEW
                assert row.matched_influencer_id is None
                assert row.matched_platform_account_id is None
                manual_review = row.merge_plan["batch_manual_review"]
                assert manual_review["reason"] == "BATCH_DATABASE_IDENTITY_CONFLICT"
                assert manual_review["rows"] == [
                    {
                        "import_job_file_id": str(occurrence.id),
                        "file_position": 1,
                        "row_number": 2,
                        "import_row_id": str(row.id),
                    }
                ]
                assert manual_review["conflict_fields"] == []
                assert manual_review["hard_identity_keys"] == sorted(
                    manual_review["hard_identity_keys"]
                )
                assert manual_review["database_identity_candidates"] == sorted(
                    manual_review["database_identity_candidates"],
                    key=lambda candidate: (
                        candidate["hard_identity"],
                        candidate["platform_account_id"],
                        candidate["influencer_id"],
                    ),
                )
                evidence = repr((manual_review, row.warnings, row.errors))
                assert str(first.id) in evidence
                assert str(second.id) in evidence
                assert secret_contact not in evidence
                assert await _business_counts(session) == baseline

    asyncio.run(scenario())
