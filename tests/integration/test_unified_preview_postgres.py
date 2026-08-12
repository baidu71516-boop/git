"""PostgreSQL 16 gates for the Phase 2 Unified Preview builder.

The module accepts only an explicitly named disposable test database.  Every
test creates and drops a random isolated schema, so no development or
production object is ever selected or mutated.  All row data is synthetic.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import resource
import sys
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import pytest
from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, Operator
from backend_core.auth.service import AuthContext
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.batch_processor import BatchImportProcessor
from backend_core.imports.contracts import CanonicalInfluencerRecord
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobFailedStage,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportMatchType,
    ImportRowAction,
    ImportSourceType,
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
    StoredImportFile,
)
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.preview_processor import UnifiedPreviewProcessor
from backend_core.imports.repository import ImportRepository
from backend_core.imports.schemas import ImportRowCategory
from backend_core.imports.service import ImportService
from backend_core.imports.storage import LocalStorageAdapter
from backend_core.influencers.enums import DataSource, Platform
from backend_core.influencers.models import (
    Influencer,
    InfluencerCurrentMetrics,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema, DropSchema

PERFORMANCE_ENV = "RUN_IMPORT_PERFORMANCE_TESTS"
SOURCE_TIME = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)
MAPPING = {
    "name": "nickname",
    "account_id": "platform_account_id",
    "external_id": "external_source_id",
    "updated_at": "source_updated_at",
    "followers": "followers_count",
    "tags": "creator_tags",
    "email": "email",
}
CSV_HEADERS = (*MAPPING, "fixture_nonce")


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


@dataclass(frozen=True, slots=True)
class SqlMeasurement:
    rows: int
    total: int
    selects: int
    dml: int
    advisory: int
    wall_seconds: float
    rss_high_water_before_bytes: int
    rss_high_water_after_bytes: int

    @property
    def rss_high_water_delta_bytes(self) -> int:
        return max(0, self.rss_high_water_after_bytes - self.rss_high_water_before_bytes)


class SqlProbe:
    """Count builder SQL and retain the process RSS measurement qualifier."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.active = False
        self.total = 0
        self.selects = 0
        self.dml = 0
        self.advisory = 0
        self.started_at = 0.0
        self.rss_before = 0
        event.listen(engine.sync_engine, "before_cursor_execute", self._statement)

    def _statement(
        self,
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if not self.active:
            return
        normalized = statement.lstrip().upper()
        self.total += 1
        if normalized.startswith("SELECT"):
            self.selects += 1
        if normalized.startswith(("INSERT", "UPDATE", "DELETE")):
            self.dml += 1
        if "PG_ADVISORY_XACT_LOCK" in normalized:
            self.advisory += 1

    def start(self) -> None:
        assert not self.active
        self.total = 0
        self.selects = 0
        self.dml = 0
        self.advisory = 0
        self.started_at = perf_counter()
        self.rss_before = _peak_rss_bytes()
        self.active = True

    def stop(self, *, rows: int) -> SqlMeasurement:
        assert self.active
        wall_seconds = perf_counter() - self.started_at
        self.active = False
        return SqlMeasurement(
            rows=rows,
            total=self.total,
            selects=self.selects,
            dml=self.dml,
            advisory=self.advisory,
            wall_seconds=wall_seconds,
            rss_high_water_before_bytes=self.rss_before,
            rss_high_water_after_bytes=_peak_rss_bytes(),
        )

    def close(self) -> None:
        event.remove(self.engine.sync_engine, "before_cursor_execute", self._statement)


@dataclass(frozen=True, slots=True)
class PostgresHarness:
    factory: async_sessionmaker[AsyncSession]
    storage: LocalStorageAdapter
    probe: SqlProbe


@dataclass(frozen=True, slots=True)
class SeededBatch:
    job_id: UUID
    department_id: UUID
    operator_id: UUID
    auth_session_id: UUID
    files: tuple[ImportJobFile, ...]


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _emit(label: str, measurement: SqlMeasurement) -> None:
    print(
        "TASK5_PREVIEW_SQL "
        + json.dumps(
            {
                "label": label,
                "rows": measurement.rows,
                "total": measurement.total,
                "select": measurement.selects,
                "dml": measurement.dml,
                "advisory": measurement.advisory,
                "wall_seconds": round(measurement.wall_seconds, 6),
                # This is a process-lifetime high-water delta, not an
                # instantaneous resident-set measurement.
                "rss_high_water_delta_bytes": measurement.rss_high_water_delta_bytes,
                "rss_high_water_after_bytes": measurement.rss_high_water_after_bytes,
            },
            sort_keys=True,
        )
    )


@asynccontextmanager
async def _isolated_postgres() -> AsyncIterator[PostgresHarness]:
    schema_name = f"phase2_unified_preview_{uuid4().hex}"
    admin_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    test_engine: AsyncEngine | None = None
    probe: SqlProbe | None = None
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
        probe = SqlProbe(test_engine)
        factory = async_sessionmaker(test_engine, expire_on_commit=False)
        with TemporaryDirectory(prefix="phase2-unified-preview-postgres-") as directory:
            yield PostgresHarness(factory, LocalStorageAdapter(Path(directory)), probe)
    finally:
        if probe is not None:
            probe.close()
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
    updated_at: str = "2026-08-10 12:00:00",
    followers: str = "200",
    tags: str = "beauty",
    email: str = "",
    nonce: str = "fixture",
) -> dict[str, str]:
    return {
        "name": name or f"Fixture {account_id}",
        "account_id": account_id,
        "external_id": external_id,
        "updated_at": updated_at,
        "followers": followers,
        "tags": tags,
        "email": email,
        "fixture_nonce": nonce,
    }


async def _store_csv(
    session: AsyncSession,
    storage: LocalStorageAdapter,
    rows: Iterable[dict[str, str]],
) -> StoredImportFile:
    stored = await storage.store(
        _one_chunk(_csv_bytes(rows)), suffix=".csv", max_bytes=25 * 1024 * 1024
    )
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
    harness: PostgresHarness,
    files: Iterable[Iterable[dict[str, str]]],
    *,
    screening: bool = False,
) -> SeededBatch:
    rows_by_file = [list(rows) for rows in files]
    async with harness.factory() as session:
        department = Department(
            name=f"Unified Preview fixture {uuid4().hex}",
            password_hash="not-used-by-integration-test",
            status=DepartmentStatus.ACTIVE,
            session_days=30,
        )
        session.add(department)
        await session.flush()
        operator = Operator(
            department_id=department.id,
            name="Unified Preview operator",
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
            user_agent="Task5 PostgreSQL integration",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(auth_session)
        collection = CollectionJob(
            name="Task 5 PostgreSQL collection",
            industry="must-not-drive-screening",
            subdirection="must-not-drive-screening",
            purpose="verify Unified Preview",
            target_action="preview",
            follower_min=100 if screening else None,
            follower_max=1_000 if screening else None,
            target_count=max(sum(map(len, rows_by_file)), 1),
            department_id=department.id,
            owner_operator_id=operator.id,
            source_type=ImportSourceType.GENERIC_CSV,
            status=CollectionJobStatus.ACTIVE,
            screening_rules=(
                {
                    "schema_version": 1,
                    "platforms": [Platform.XIAOHONGSHU.value],
                    "source_tags_exact_any": ["beauty"],
                }
                if screening
                else {
                    "schema_version": 1,
                    "platforms": [],
                    "source_tags_exact_any": [],
                }
            ),
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
        occurrences: list[ImportJobFile] = []
        for position, rows in enumerate(rows_by_file, start=1):
            stored_file = await _store_csv(session, harness.storage, rows)
            task_id = f"parse-{job.id}-{position}"
            occurrence = ImportJobFile(
                import_job_id=job.id,
                stored_file_id=stored_file.id,
                position=position,
                original_filename=f"synthetic-{position}.csv",
                declared_mime="text/csv",
                status=ImportJobFileStatus.PARSING,
                source_acquired_at=SOURCE_TIME,
                source_acquired_at_origin=SourceAcquiredAtOrigin.USER_CONFIRMED,
                source_acquired_at_confirmation_required=False,
                field_mapping=dict(MAPPING),
                parse_task_id=task_id,
                parse_attempts=1,
                parse_started_at=datetime.now(UTC),
            )
            session.add(occurrence)
            occurrences.append(occurrence)
        await session.commit()
        return SeededBatch(
            job.id,
            department.id,
            operator.id,
            auth_session.id,
            tuple(occurrences),
        )


def _batch_processor(session: AsyncSession, storage: LocalStorageAdapter) -> BatchImportProcessor:
    return BatchImportProcessor(
        session,
        storage,
        parser_limits=ParserLimits(max_rows=10_000, max_columns=100, max_cells=1_000_000),
        max_batch_rows=10_000,
    )


async def _parse_all(harness: PostgresHarness, seeded: SeededBatch) -> None:
    for occurrence in seeded.files:
        async with harness.factory() as session:
            result = await _batch_processor(session, harness.storage).parse_file(
                seeded.job_id, occurrence.id, str(occurrence.parse_task_id)
            )
            assert result["status"] == ImportJobFileStatus.READY.value


async def _context(session: AsyncSession, seeded: SeededBatch) -> AuthContext:
    department = await session.get(Department, seeded.department_id)
    operator = await session.get(Operator, seeded.operator_id)
    auth_session = await session.get(AuthSession, seeded.auth_session_id)
    assert department is not None and operator is not None and auth_session is not None
    return AuthContext(department, operator, Role.OPERATOR, auth_session)


def _service(session: AsyncSession, storage: LocalStorageAdapter) -> ImportService:
    return ImportService(
        session,
        storage,
        parser_limits=ParserLimits(max_rows=10_000, max_columns=100, max_cells=1_000_000),
        max_file_bytes=25 * 1024 * 1024,
        retention_days=30,
        max_batch_rows=10_000,
    )


async def _request_preview(
    harness: PostgresHarness,
    seeded: SeededBatch,
    task_id: str,
    *,
    rebuild: bool = False,
) -> tuple[bool, bool, str]:
    async with harness.factory() as session:
        decision = await _service(session, harness.storage).request_preview(
            await _context(session, seeded),
            seeded.job_id,
            task_id,
            ip="127.0.0.1",
            user_agent="Task5 PostgreSQL integration",
            rebuild=rebuild,
        )
        return decision.should_dispatch, decision.idempotent, decision.task_id


async def _build(
    harness: PostgresHarness,
    job_id: UUID,
    task_id: str,
) -> dict[str, Any]:
    async with harness.factory() as session:
        return await UnifiedPreviewProcessor(
            session,
            harness.storage,
            parser_limits=ParserLimits(max_rows=10_000, max_columns=100, max_cells=1_000_000),
            max_batch_rows=10_000,
        ).build(job_id, task_id)


async def _job_rows(session: AsyncSession, job_id: UUID) -> list[ImportRow]:
    return list(
        await session.scalars(
            select(ImportRow)
            .join(ImportJobFile, ImportJobFile.id == ImportRow.import_job_file_id)
            .where(ImportRow.import_job_id == job_id)
            .order_by(ImportJobFile.position, ImportRow.row_number, ImportRow.id)
        )
    )


def _matrix_files() -> list[list[dict[str, str]]]:
    files = [
        [
            _row(
                f"matrix-{position}-{index}",
                nonce=f"file-{position}-row-{index}",
            )
            for index in range(500)
        ]
        for position in range(1, 5)
    ]
    files[0][1].update({"account_id": "existing-no-change", "name": "No change"})
    files[0][2].update({"account_id": "existing-changed", "name": "Changed"})
    files[0][3].update(
        {
            "account_id": "manual-account",
            "external_id": "manual-external",
            "name": "Manual review",
        }
    )
    files[0][4].update({"account_id": "", "external_id": "", "name": "Error row"})
    files[0][5].update({"followers": "not-an-integer", "name": "Unknown screening"})
    files[0][6].update({"tags": "fashion", "name": "Not matching screening"})
    files[0][7].update({"email": "shared-contact@example.invalid"})
    files[0][8].update({"email": "shared-contact@example.invalid"})
    files[1][499] = {**files[0][0], "fixture_nonce": "cross-file-duplicate-one"}
    files[3][499] = {**files[2][0], "fixture_nonce": "cross-file-duplicate-two"}
    return files


async def _account(
    session: AsyncSession,
    *,
    platform_account_id: str,
    display_name: str,
) -> tuple[Influencer, InfluencerPlatformAccount]:
    influencer = Influencer(display_name=display_name)
    session.add(influencer)
    await session.flush()
    account = InfluencerPlatformAccount(
        influencer_id=influencer.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id=platform_account_id,
        account_name=display_name,
        source=DataSource.GENERIC,
        is_active=True,
    )
    session.add(account)
    await session.flush()
    return influencer, account


async def _seed_completed_observation(
    session: AsyncSession,
    seeded: SeededBatch,
    account: InfluencerPlatformAccount,
    *,
    observed_at: datetime,
    label: str,
    action: ImportRowAction = ImportRowAction.NO_CHANGE,
    job_stored_file_id: UUID | None = None,
    file_status: ImportJobFileStatus = ImportJobFileStatus.READY,
) -> None:
    current_job = await session.get(ImportJob, seeded.job_id)
    assert current_job is not None
    history_job = ImportJob(
        collection_job_id=current_job.collection_job_id,
        department_id=current_job.department_id,
        operator_id=current_job.operator_id,
        stored_file_id=job_stored_file_id,
        source_type=ImportSourceType.GENERIC_CSV,
        status=ImportJobStatus.COMPLETED,
        preview_revision=1,
        confirmed_revision=1,
        confirmed_at=observed_at + timedelta(minutes=1),
        completed_at=observed_at + timedelta(minutes=2),
    )
    session.add(history_job)
    await session.flush()
    occurrence = ImportJobFile(
        import_job_id=history_job.id,
        stored_file_id=seeded.files[0].stored_file_id,
        position=1,
        original_filename=f"history-{label}.csv",
        declared_mime="text/csv",
        status=file_status,
        source_acquired_at=observed_at,
        source_acquired_at_origin=SourceAcquiredAtOrigin.USER_CONFIRMED,
        source_acquired_at_confirmation_required=False,
        raw_rows=1,
        parse_attempts=1,
    )
    session.add(occurrence)
    await session.flush()
    session.add(
        ImportRow(
            import_job_id=history_job.id,
            import_job_file_id=occurrence.id,
            row_number=2,
            raw_data={"fixture": label},
            normalized_data={"fixture": label},
            matched_influencer_id=account.influencer_id,
            matched_platform_account_id=account.id,
            match_type=ImportMatchType.PLATFORM_ACCOUNT_ID,
            action=action,
            merge_plan={},
            warnings=[],
            errors=[],
            preview_revision=1,
            plan_hash=hash_document({"history": label}),
            committed_action=action,
            committed_at=observed_at + timedelta(minutes=2),
        )
    )


async def _seed_matrix_database_state(
    harness: PostgresHarness,
    seeded: SeededBatch,
) -> None:
    async with harness.factory() as session:
        rows = await _job_rows(session, seeded.job_id)
        by_account = {
            row.normalized_data["platform_identity"]["platform_account_id"]: row
            for row in rows
            if row.normalized_data is not None
        }
        no_change_row = by_account["existing-no-change"]
        no_change_record = CanonicalInfluencerRecord.model_validate(no_change_row.normalized_data)
        influencer, account = await _account(
            session,
            platform_account_id="existing-no-change",
            display_name="No change",
        )
        account.source_tags = ["beauty"]
        source_data = {"display_name": "No change", "creator_tags": ["beauty"]}
        metrics = {
            key: value
            for key, value in no_change_record.as_dict()["metrics"].items()
            if value is not None
        }
        session.add_all(
            (
                InfluencerSourceState(
                    influencer_id=influencer.id,
                    platform_account_id=account.id,
                    source=DataSource.GENERIC,
                    source_updated_at=no_change_record.source_updated_at,
                    source_data=source_data,
                    source_data_hash=hash_document(source_data),
                    state_version=1,
                    last_import_job_id=seeded.job_id,
                    last_import_row_id=no_change_row.id,
                ),
                InfluencerCurrentMetrics(
                    influencer_id=influencer.id,
                    platform_account_id=account.id,
                    source=DataSource.GENERIC,
                    source_updated_at=no_change_record.source_updated_at,
                    metrics=metrics,
                    metrics_hash=hash_document(metrics),
                    last_import_job_id=seeded.job_id,
                    last_import_row_id=no_change_row.id,
                ),
            )
        )
        await _account(
            session,
            platform_account_id="existing-changed",
            display_name="Changed",
        )
        await _account(
            session,
            platform_account_id="manual-account",
            display_name="Manual platform match",
        )
        _, external_account = await _account(
            session,
            platform_account_id="manual-other",
            display_name="Manual external match",
        )
        manual_row = by_account["manual-account"]
        session.add(
            PlatformAccountSourceIdentity(
                platform_account_id=external_account.id,
                platform=Platform.XIAOHONGSHU,
                source=DataSource.GENERIC,
                external_account_id="manual-external",
                first_import_job_id=seeded.job_id,
                first_import_row_id=manual_row.id,
                last_import_job_id=seeded.job_id,
                last_import_row_id=manual_row.id,
            )
        )
        await session.commit()


def test_same_token_double_request_and_double_worker_converge_one_revision() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            seeded = await _seed_batch(harness, [[_row(f"replay-{index}") for index in range(20)]])
            await _parse_all(harness, seeded)
            task_id = uuid4().hex
            requests = await asyncio.gather(
                _request_preview(harness, seeded, task_id),
                _request_preview(harness, seeded, task_id),
            )
            assert sorted(item[0] for item in requests) == [False, True]
            assert sorted(item[1] for item in requests) == [False, True]
            assert {item[2] for item in requests} == {task_id}

            results = await asyncio.wait_for(
                asyncio.gather(
                    _build(harness, seeded.job_id, task_id),
                    _build(harness, seeded.job_id, task_id),
                ),
                timeout=20,
            )
            assert sorted(item["idempotent"] for item in results) == [False, True]
            assert {item["preview_revision"] for item in results} == {1}
            async with harness.factory() as session:
                job = await session.get(ImportJob, seeded.job_id)
                assert job is not None
                assert job.status is ImportJobStatus.PREVIEW_READY
                assert job.preview_revision == 1
                assert job.preview_summary is not None
                rows = await _job_rows(session, job.id)
                assert len(rows) == 20
                assert {row.preview_revision for row in rows} == {1}
                audit_count = await session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.entity_id == job.id,
                        AuditLog.action == AuditAction.IMPORT_BATCH_PREVIEW_CREATED,
                    )
                )
                assert audit_count == 1

    asyncio.run(scenario())


def test_freshness_uses_confirmed_account_source_history_and_survives_rebuild() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            seeded = await _seed_batch(
                harness,
                [
                    [
                        _row("fresh-new"),
                        _row("fresh-advanced"),
                        _row("fresh-stale"),
                    ]
                ],
            )
            await _parse_all(harness, seeded)
            earlier = SOURCE_TIME - timedelta(days=2)
            later = SOURCE_TIME + timedelta(days=2)
            async with harness.factory() as session:
                _, advanced_account = await _account(
                    session,
                    platform_account_id="fresh-advanced",
                    display_name="Fixture fresh-advanced",
                )
                _, stale_account = await _account(
                    session,
                    platform_account_id="fresh-stale",
                    display_name="Fixture fresh-stale",
                )
                await _seed_completed_observation(
                    session,
                    seeded,
                    advanced_account,
                    observed_at=earlier,
                    label="earlier",
                )
                await _seed_completed_observation(
                    session,
                    seeded,
                    advanced_account,
                    observed_at=SOURCE_TIME + timedelta(days=4),
                    label="manual-review-must-not-advance",
                    action=ImportRowAction.MANUAL_REVIEW,
                )
                await _seed_completed_observation(
                    session,
                    seeded,
                    advanced_account,
                    observed_at=SOURCE_TIME + timedelta(days=5),
                    label="legacy-lineage-must-not-advance",
                    job_stored_file_id=seeded.files[0].stored_file_id,
                )
                await _seed_completed_observation(
                    session,
                    seeded,
                    advanced_account,
                    observed_at=SOURCE_TIME + timedelta(days=6),
                    label="non-ready-lineage-must-not-advance",
                    file_status=ImportJobFileStatus.FAILED,
                )
                await _seed_completed_observation(
                    session,
                    seeded,
                    stale_account,
                    observed_at=later,
                    label="later",
                )
                await session.commit()

            first_task = uuid4().hex
            assert (await _request_preview(harness, seeded, first_task))[0] is True
            await _build(harness, seeded.job_id, first_task)

            async def freshness_by_account() -> dict[str, list[dict[str, Any]]]:
                async with harness.factory() as session:
                    rows = await _job_rows(session, seeded.job_id)
                    return {
                        row.normalized_data["platform_identity"]["platform_account_id"]: (
                            row.merge_plan["change_summary"]["freshness_changes"]
                        )
                        for row in rows
                        if row.normalized_data is not None and row.merge_plan is not None
                    }

            first = await freshness_by_account()
            assert first["fresh-new"][0]["before"] is None
            assert first["fresh-new"][0]["after"] == SOURCE_TIME.isoformat().replace("+00:00", "Z")
            assert first["fresh-advanced"][0]["before"] == earlier.isoformat().replace(
                "+00:00", "Z"
            )
            assert first["fresh-advanced"][0]["after"] == SOURCE_TIME.isoformat().replace(
                "+00:00", "Z"
            )
            assert first["fresh-stale"] == []

            delayed_task = uuid4().hex
            delayed = await _request_preview(harness, seeded, delayed_task)
            assert delayed == (False, True, first_task)
            async with harness.factory() as session:
                job = await session.get(ImportJob, seeded.job_id)
                assert job is not None and job.preview_revision == 1

            rebuild_task = uuid4().hex
            assert (await _request_preview(harness, seeded, rebuild_task, rebuild=True)) == (
                True,
                False,
                rebuild_task,
            )
            await _build(harness, seeded.job_id, rebuild_task)
            second = await freshness_by_account()
            assert second == first
            async with harness.factory() as session:
                job = await session.get(ImportJob, seeded.job_id)
                assert job is not None and job.preview_revision == 2

    asyncio.run(scenario())


def test_failed_rebuild_rolls_back_and_keeps_previous_complete_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            seeded = await _seed_batch(
                harness, [[_row(f"rollback-{index}") for index in range(25)]]
            )
            await _parse_all(harness, seeded)
            first_task = uuid4().hex
            assert (await _request_preview(harness, seeded, first_task))[0] is True
            await _build(harness, seeded.job_id, first_task)
            async with harness.factory() as session:
                job = await session.get(ImportJob, seeded.job_id)
                assert job is not None and job.preview_summary is not None
                old_summary = dict(job.preview_summary)
                old_hashes = [(row.id, row.plan_hash) for row in await _job_rows(session, job.id)]

            second_task = uuid4().hex
            assert (await _request_preview(harness, seeded, second_task, rebuild=True))[0] is True

            def fail_before_commit(*_args: object, **_kwargs: object) -> str:
                raise RuntimeError("injected Task 5 atomicity failure")

            monkeypatch.setattr(
                "backend_core.imports.preview_processor.hash_batch_plan", fail_before_commit
            )
            with pytest.raises(ImportDomainError) as raised:
                await _build(harness, seeded.job_id, second_task)
            assert raised.value.code == "IMPORT_PREVIEW_FAILED"

            async with harness.factory() as session:
                job = await session.get(ImportJob, seeded.job_id)
                assert job is not None
                assert job.status is ImportJobStatus.FAILED
                assert job.failed_stage is ImportJobFailedStage.PREVIEW
                assert job.preview_revision == 1
                assert job.preview_summary == old_summary
                rows = await _job_rows(session, job.id)
                assert [(row.id, row.plan_hash) for row in rows] == old_hashes
                assert {row.preview_revision for row in rows} == {1}

    asyncio.run(scenario())


def test_four_by_500_preview_summary_categories_pagination_and_query_count() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            seeded = await _seed_batch(harness, _matrix_files(), screening=True)
            await _parse_all(harness, seeded)
            await _seed_matrix_database_state(harness, seeded)
            task_id = uuid4().hex
            assert (await _request_preview(harness, seeded, task_id))[0] is True

            harness.probe.start()
            result = await _build(harness, seeded.job_id, task_id)
            measurement = harness.probe.stop(rows=2_000)
            _emit("4x500-unified-preview", measurement)
            assert result["status"] == ImportJobStatus.PREVIEW_READY.value
            assert result["preview_revision"] == 1
            assert measurement.wall_seconds <= 60
            assert measurement.selects <= 60
            assert measurement.total <= 80
            assert measurement.rss_high_water_after_bytes <= 900 * 1024**2

            async with harness.factory() as session:
                job = await session.get(ImportJob, seeded.job_id)
                assert job is not None and job.preview_summary is not None
                expected = {
                    "file_count": 4,
                    "occurrence_count": 4,
                    "excluded_file_count": 0,
                    "raw_rows": 2_000,
                    "unique_rows": 1_998,
                    "internal_duplicate_rows": 2,
                    "existing_rows": 2,
                    "new_rows": 1_994,
                    "changed_rows": 1,
                    "no_change_rows": 1,
                    "created_rows": 1_994,
                    "updated_rows": 1,
                    "skipped_rows": 2,
                    "error_rows": 1,
                    "manual_review_rows": 1,
                    "warning_rows": 4,
                    "possible_duplicate_contact_rows": 2,
                    "screened_rows": 1_997,
                    "screening_match_rows": 1_995,
                    "screening_not_match_rows": 1,
                    "screening_unknown_rows": 1,
                }
                assert {key: job.preview_summary[key] for key in expected} == expected
                assert len(job.preview_summary["context_hash"]) == 64
                assert len(job.preview_summary["batch_plan_hash"]) == 64
                rows = await _job_rows(session, seeded.job_id)
                assert len(rows) == 2_000
                assert {row.preview_revision for row in rows} == {1}
                assert all(len(row.plan_hash) == 64 for row in rows)
                for row in rows:
                    if row.action in {
                        ImportRowAction.ERROR,
                        ImportRowAction.SKIP,
                        ImportRowAction.MANUAL_REVIEW,
                    }:
                        assert row.merge_plan is not None
                        assert row.merge_plan["change_summary"]["freshness_changes"] == []

                repository = ImportRepository(session)
                expected_category_totals = {
                    ImportRowCategory.ATTENTION: 5,
                    ImportRowCategory.ERROR: 1,
                    ImportRowCategory.MANUAL_REVIEW: 1,
                    ImportRowCategory.WARNING: 4,
                    ImportRowCategory.CHANGED: 1,
                    ImportRowCategory.NEW: 1_994,
                    ImportRowCategory.NO_CHANGE: 1,
                    ImportRowCategory.DUPLICATE: 2,
                    ImportRowCategory.ALL: 2_000,
                }
                harness.probe.start()
                for category, expected_total in expected_category_totals.items():
                    page, total = await repository.list_import_rows(
                        seeded.job_id,
                        offset=0,
                        limit=37,
                        category=category,
                    )
                    assert total == expected_total
                    assert len(page) == min(37, expected_total)
                category_measurement = harness.probe.stop(rows=2_000)
                assert category_measurement.total == 2 * len(expected_category_totals)
                assert category_measurement.selects == category_measurement.total

                positions = {item.id: item.position for item in seeded.files}
                first_pass: list[tuple[int, int, UUID]] = []
                for offset in range(0, 2_000, 100):
                    page, total = await repository.list_import_rows(
                        seeded.job_id,
                        offset=offset,
                        limit=100,
                        category=ImportRowCategory.ALL,
                    )
                    assert total == 2_000
                    first_pass.extend(
                        (positions[row.import_job_file_id], row.row_number, row.id) for row in page
                    )
                second_pass: list[tuple[int, int, UUID]] = []
                for offset in range(0, 2_000, 100):
                    page, _ = await repository.list_import_rows(
                        seeded.job_id,
                        offset=offset,
                        limit=100,
                        category=ImportRowCategory.ALL,
                    )
                    second_pass.extend(
                        (positions[row.import_job_file_id], row.row_number, row.id) for row in page
                    )
                assert first_pass == second_pass
                assert first_pass == sorted(first_pass)
                assert len({item[2] for item in first_pass}) == 2_000

                attention, attention_total = await repository.list_import_rows(
                    seeded.job_id,
                    offset=0,
                    limit=100,
                    category=ImportRowCategory.ATTENTION,
                )
                assert attention_total == 5
                assert attention[0].action is ImportRowAction.ERROR
                assert attention[1].action is ImportRowAction.MANUAL_REVIEW

    asyncio.run(scenario())


def test_preview_waits_for_same_identity_writer_without_deadlock() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            seeded = await _seed_batch(harness, [[_row("writer-race", name="Writer race")]])
            await _parse_all(harness, seeded)
            task_id = uuid4().hex
            assert (await _request_preview(harness, seeded, task_id))[0] is True
            writer_locked = asyncio.Event()
            release_writer = asyncio.Event()
            written_account_id: UUID | None = None

            async def writer() -> None:
                nonlocal written_account_id
                async with harness.factory() as session:
                    await ImportRepository(session).acquire_identity_locks(
                        ["platform:xiaohongshu:account:writer-race"]
                    )
                    writer_locked.set()
                    await release_writer.wait()
                    _, account = await _account(
                        session,
                        platform_account_id="writer-race",
                        display_name="Writer race",
                    )
                    written_account_id = account.id
                    await session.commit()

            writer_task = asyncio.create_task(writer())
            await writer_locked.wait()
            preview_task = asyncio.create_task(_build(harness, seeded.job_id, task_id))
            await asyncio.sleep(0.05)
            assert not preview_task.done()
            release_writer.set()
            await asyncio.wait_for(asyncio.gather(writer_task, preview_task), timeout=15)
            assert written_account_id is not None

            async with harness.factory() as session:
                row = (await _job_rows(session, seeded.job_id))[0]
                assert row.matched_platform_account_id == written_account_id
                assert row.action is ImportRowAction.UPDATE
                assert row.merge_plan is not None
                assert row.merge_plan["preview_context_hash"]

    asyncio.run(scenario())


def test_full_preview_builder_5k_and_10k_capacity_no_oom_gate() -> None:
    if os.environ.get(PERFORMANCE_ENV) != "1":
        pytest.skip(f"set {PERFORMANCE_ENV}=1 for the full 5k/10k Preview capacity gates")

    async def scenario() -> None:
        for row_count in (5_000, 10_000):
            async with _isolated_postgres() as harness:
                seeded = await _seed_batch(
                    harness,
                    [[_row(f"capacity-{row_count}-{index}") for index in range(row_count)]],
                )
                await _parse_all(harness, seeded)
                task_id = uuid4().hex
                assert (await _request_preview(harness, seeded, task_id))[0] is True
                harness.probe.start()
                await _build(harness, seeded.job_id, task_id)
                measurement = harness.probe.stop(rows=row_count)
                _emit(f"capacity-{row_count}-unified-preview", measurement)
                chunks = (row_count + 499) // 500
                assert measurement.selects <= 15 + (5 * chunks)
                assert measurement.total <= 25 + (6 * chunks)
                assert measurement.rss_high_water_after_bytes <= 900 * 1024**2
                async with harness.factory() as session:
                    job = await session.get(ImportJob, seeded.job_id)
                    assert job is not None and job.preview_summary is not None
                    assert job.preview_summary["raw_rows"] == row_count
                    assert job.preview_summary["unique_rows"] == row_count
                    assert job.preview_summary["new_rows"] == row_count
                    assert job.preview_summary["screening_unknown_rows"] == row_count
                    rows = await _job_rows(session, seeded.job_id)
                    assert len(rows) == row_count
                    assert {row.preview_revision for row in rows} == {1}
                    assert len({row.plan_hash for row in rows}) == row_count

    asyncio.run(scenario())
