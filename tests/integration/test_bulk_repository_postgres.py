"""PostgreSQL 16 performance gates for Phase 2 bulk repository primitives.

The module accepts only an explicitly named disposable test database.  Every
test creates and drops an isolated schema.  Synthetic values use reserved
``.invalid`` contacts and contain no real personal data.
"""

from __future__ import annotations

import asyncio
import json
import os
import resource
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from time import perf_counter
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, Operator
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.bulk_repository import (
    BulkImportContext,
    BulkImportRepository,
    PrefetchedImportRepository,
    PrefetchedImportState,
)
from backend_core.imports.contracts import (
    CanonicalContact,
    CanonicalInfluencerRecord,
    PlatformIdentity,
)
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
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportRow,
    StoredImportFile,
)
from backend_core.imports.planner import ImportPlanner
from backend_core.imports.repository import ImportRepository
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    CRMStage,
    DataSource,
    InfluencerStatus,
    Platform,
)
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)
from sqlalchemy import event, insert, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

SOURCE_TIME = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)
CHUNK_SIZE = 500
PERFORMANCE_ENV = "RUN_IMPORT_PERFORMANCE_TESTS"


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


class ProbeSession(Session):
    """Dedicated synchronous session class used only for event telemetry."""


@dataclass(frozen=True, slots=True)
class SqlMeasurement:
    label: str
    rows: int
    total: int
    selects: int
    dml: int
    advisory: int
    flushes: int
    transactions: int
    wall_seconds: float
    peak_rss_before_bytes: int
    peak_rss_after_bytes: int

    @property
    def peak_rss_high_water_delta_bytes(self) -> int:
        return max(0, self.peak_rss_after_bytes - self.peak_rss_before_bytes)


class SqlProbe:
    """Collect SQL, flush, transaction, wall, and process high-water RSS data."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.active = False
        self.total = 0
        self.selects = 0
        self.dml = 0
        self.advisory = 0
        self.flushes = 0
        self.transactions = 0
        self.started_at = 0.0
        self.peak_rss_before_bytes = 0
        event.listen(engine.sync_engine, "before_cursor_execute", self._statement)
        event.listen(engine.sync_engine, "begin", self._transaction)
        event.listen(ProbeSession, "before_flush", self._flush)

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

    def _transaction(self, _connection: object) -> None:
        if self.active:
            self.transactions += 1

    def _flush(self, _session: Session, _flush_context: object, _instances: object) -> None:
        if self.active:
            self.flushes += 1

    def start(self) -> None:
        assert not self.active
        self.total = 0
        self.selects = 0
        self.dml = 0
        self.advisory = 0
        self.flushes = 0
        self.transactions = 0
        self.peak_rss_before_bytes = _peak_rss_bytes()
        self.started_at = perf_counter()
        self.active = True

    def stop(self, *, label: str, rows: int) -> SqlMeasurement:
        assert self.active
        wall_seconds = perf_counter() - self.started_at
        self.active = False
        return SqlMeasurement(
            label=label,
            rows=rows,
            total=self.total,
            selects=self.selects,
            dml=self.dml,
            advisory=self.advisory,
            flushes=self.flushes,
            transactions=self.transactions,
            wall_seconds=wall_seconds,
            peak_rss_before_bytes=self.peak_rss_before_bytes,
            peak_rss_after_bytes=_peak_rss_bytes(),
        )

    def close(self) -> None:
        event.remove(self.engine.sync_engine, "before_cursor_execute", self._statement)
        event.remove(self.engine.sync_engine, "begin", self._transaction)
        event.remove(ProbeSession, "before_flush", self._flush)


@dataclass(frozen=True, slots=True)
class PostgresHarness:
    factory: async_sessionmaker[AsyncSession]
    probe: SqlProbe


def _peak_rss_bytes() -> int:
    """Return the process RSS high-water mark using the host platform's unit."""

    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _emit(measurement: SqlMeasurement) -> None:
    """Make explicit performance runs produce copyable, machine-readable evidence."""

    print(
        "TASK4_SQL "
        + json.dumps(
            {
                "label": measurement.label,
                "rows": measurement.rows,
                "total": measurement.total,
                "select": measurement.selects,
                "dml": measurement.dml,
                "advisory": measurement.advisory,
                "flush": measurement.flushes,
                "tx": measurement.transactions,
                "wall_seconds": round(measurement.wall_seconds, 6),
                # ru_maxrss is a process-lifetime high-water mark, not a
                # per-operation resident-set sample; the report must retain
                # this explicit measurement qualifier.
                "rss_high_water_delta_bytes": measurement.peak_rss_high_water_delta_bytes,
            },
            sort_keys=True,
        )
    )


@asynccontextmanager
async def _isolated_postgres() -> AsyncIterator[PostgresHarness]:
    schema_name = f"phase2_bulk_repository_{uuid4().hex}"
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
        factory = async_sessionmaker(
            test_engine,
            expire_on_commit=False,
            sync_session_class=ProbeSession,
        )
        probe = SqlProbe(test_engine)
        yield PostgresHarness(factory=factory, probe=probe)
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


def _fixture_uuid(kind: str, index: int = 0) -> UUID:
    return uuid5(NAMESPACE_URL, f"phase2-task4:{kind}:{index}")


def _snapshot_key(prefix: str, index: int) -> str:
    return sha256(f"phase2-task4:{prefix}:snapshot:{index}".encode()).hexdigest()


def _record(
    index: int,
    *,
    prefix: str,
    identity_modulo: int | None = None,
    email_modulo: int | None = None,
) -> CanonicalInfluencerRecord:
    identity_index = index if identity_modulo is None else index % identity_modulo
    email_index = index if email_modulo is None else index % email_modulo
    account_id = f"{prefix}-account-{identity_index}"
    profile_url = f"https://example.invalid/{prefix}/profile/{identity_index}"
    email = f"{prefix}-contact-{email_index}@example.invalid"
    return CanonicalInfluencerRecord(
        display_name=f"Synthetic {prefix} {identity_index}",
        platform_identity=PlatformIdentity(
            platform=Platform.XIAOHONGSHU,
            platform_account_id=account_id,
            account_handle=None,
            profile_url=profile_url,
            normalized_profile_url=profile_url,
            external_source_id=f"{prefix}-external-{identity_index}",
        ),
        source=DataSource.HUITUN,
        source_updated_at=SOURCE_TIME,
        metrics={"followers_count": identity_index},
        contacts=(
            CanonicalContact(
                type=ContactType.EMAIL,
                value=email,
                normalized_value=email,
                validation_status=ContactValidationStatus.VALID,
            ),
        ),
    )


def _records(
    count: int,
    *,
    prefix: str,
    start: int = 0,
    identity_modulo: int | None = None,
    email_modulo: int | None = None,
) -> tuple[CanonicalInfluencerRecord, ...]:
    return tuple(
        _record(
            index,
            prefix=prefix,
            identity_modulo=identity_modulo,
            email_modulo=email_modulo,
        )
        for index in range(start, start + count)
    )


def _snapshot_keys(prefix: str, count: int, *, start: int = 0) -> tuple[str, ...]:
    return tuple(_snapshot_key(prefix, index) for index in range(start, start + count))


async def _execute_chunks(
    session: AsyncSession,
    model: type[Any],
    rows: Sequence[dict[str, Any]],
    *,
    chunk_size: int = 1_000,
) -> None:
    for start in range(0, len(rows), chunk_size):
        await session.execute(insert(model), rows[start : start + chunk_size])


async def _seed_existing_graph(session: AsyncSession, count: int, *, prefix: str) -> None:
    """Seed every entity read by ``BulkImportRepository`` with synthetic data."""

    department_id = _fixture_uuid(f"{prefix}-department")
    operator_id = _fixture_uuid(f"{prefix}-operator")
    collection_id = _fixture_uuid(f"{prefix}-collection")
    stored_file_id = _fixture_uuid(f"{prefix}-stored-file")
    import_job_id = _fixture_uuid(f"{prefix}-import-job")
    import_job_file_id = _fixture_uuid(f"{prefix}-import-job-file")
    acquired_at = SOURCE_TIME - timedelta(hours=1)

    await session.execute(
        insert(Department),
        [
            {
                "id": department_id,
                "name": f"Task 4 synthetic department {prefix}",
                "password_hash": "not-a-real-password-hash",
                "status": DepartmentStatus.ACTIVE,
                "session_days": 1,
            }
        ],
    )
    await session.execute(
        insert(Operator),
        [
            {
                "id": operator_id,
                "department_id": department_id,
                "name": "Task 4 synthetic operator",
                "role": Role.OPERATOR,
                "status": OperatorStatus.ACTIVE,
            }
        ],
    )
    await session.execute(
        insert(CollectionJob),
        [
            {
                "id": collection_id,
                "name": "Task 4 synthetic collection",
                "industry": "synthetic",
                "purpose": "PostgreSQL bulk repository gate",
                "target_action": "test",
                "target_count": count,
                "department_id": department_id,
                "owner_operator_id": operator_id,
                "source_type": ImportSourceType.MANUAL_HUITUN_EXPORT,
                "status": CollectionJobStatus.ACTIVE,
                "screening_rules": {
                    "schema_version": 1,
                    "platforms": [],
                    "source_tags_exact_any": [],
                },
                "screening_rules_revision": 1,
            }
        ],
    )
    await session.execute(
        insert(StoredImportFile),
        [
            {
                "id": stored_file_id,
                "sha256": sha256(f"task4:{prefix}:stored".encode()).hexdigest(),
                "storage_key": f"task4/{prefix}/synthetic.csv",
                "size": 1,
                "detected_type": StoredFileType.CSV,
                "detected_mime": "text/csv",
                "expires_at": SOURCE_TIME + timedelta(days=30),
            }
        ],
    )
    await session.execute(
        insert(ImportJob),
        [
            {
                "id": import_job_id,
                "collection_job_id": collection_id,
                "department_id": department_id,
                "operator_id": operator_id,
                "stored_file_id": stored_file_id,
                "original_filename": "synthetic.csv",
                "mime_type": "text/csv",
                "file_size": 1,
                "sha256": sha256(f"task4:{prefix}:stored".encode()).hexdigest(),
                "source_type": ImportSourceType.MANUAL_HUITUN_EXPORT,
                "status": ImportJobStatus.PREVIEW_READY,
                "preview_revision": 1,
                "total_rows": count,
                "valid_rows": count,
                "warning_rows": 0,
                "error_rows": 0,
                "created_rows": 0,
                "updated_rows": 0,
                "no_change_rows": count,
                "skipped_rows": 0,
                "manual_review_rows": 0,
            }
        ],
    )
    await session.execute(
        insert(ImportJobFile),
        [
            {
                "id": import_job_file_id,
                "import_job_id": import_job_id,
                "stored_file_id": stored_file_id,
                "position": 1,
                "original_filename": "synthetic.csv",
                "declared_mime": "text/csv",
                "status": ImportJobFileStatus.READY,
                "source_acquired_at": acquired_at,
                "source_acquired_at_origin": SourceAcquiredAtOrigin.SERVER_DEFAULT,
                "source_acquired_at_confirmation_required": False,
                "raw_rows": count,
                "warning_rows": 0,
                "error_rows": 0,
                "parse_attempts": 1,
            }
        ],
    )

    influencer_rows: list[dict[str, Any]] = []
    account_rows: list[dict[str, Any]] = []
    import_rows: list[dict[str, Any]] = []
    source_identity_rows: list[dict[str, Any]] = []
    source_state_rows: list[dict[str, Any]] = []
    current_metrics_rows: list[dict[str, Any]] = []
    snapshot_rows: list[dict[str, Any]] = []
    contact_rows: list[dict[str, Any]] = []
    for index in range(count):
        influencer_id = _fixture_uuid(f"{prefix}-influencer", index)
        account_id = _fixture_uuid(f"{prefix}-account", index)
        import_row_id = _fixture_uuid(f"{prefix}-import-row", index)
        record = _record(index, prefix=prefix)
        identity = record.platform_identity
        assert identity.platform_account_id is not None
        assert identity.normalized_profile_url is not None
        assert identity.external_source_id is not None
        contact = record.contacts[0]
        metrics_hash = sha256(f"task4:{prefix}:metrics:{index}".encode()).hexdigest()
        state_hash = sha256(f"task4:{prefix}:state:{index}".encode()).hexdigest()
        influencer_rows.append(
            {
                "id": influencer_id,
                "display_name": record.display_name or "Synthetic",
                "crm_stage": CRMStage.TO_DEVELOP,
                "status": InfluencerStatus.ACTIVE,
            }
        )
        account_rows.append(
            {
                "id": account_id,
                "influencer_id": influencer_id,
                "platform": identity.platform,
                "platform_account_id": identity.platform_account_id,
                "account_name": record.display_name or "Synthetic",
                "profile_url": identity.profile_url,
                "normalized_profile_url": identity.normalized_profile_url,
                "source": record.source,
                "is_active": True,
            }
        )
        import_rows.append(
            {
                "id": import_row_id,
                "import_job_id": import_job_id,
                "import_job_file_id": import_job_file_id,
                "row_number": index + 2,
                "raw_data": {"synthetic_row": index},
                "normalized_data": {"synthetic_row": index},
                "matched_influencer_id": influencer_id,
                "matched_platform_account_id": account_id,
                "match_type": ImportMatchType.PLATFORM_ACCOUNT_ID,
                "action": ImportRowAction.NO_CHANGE,
                "warnings": [],
                "errors": [],
                "preview_revision": 1,
                "plan_hash": sha256(f"task4:{prefix}:plan:{index}".encode()).hexdigest(),
            }
        )
        source_identity_rows.append(
            {
                "id": _fixture_uuid(f"{prefix}-source-identity", index),
                "platform_account_id": account_id,
                "platform": identity.platform,
                "source": record.source,
                "external_account_id": identity.external_source_id,
                "first_import_job_id": import_job_id,
                "first_import_row_id": import_row_id,
                "last_import_job_id": import_job_id,
                "last_import_row_id": import_row_id,
            }
        )
        source_state_rows.append(
            {
                "id": _fixture_uuid(f"{prefix}-source-state", index),
                "influencer_id": influencer_id,
                "platform_account_id": account_id,
                "source": record.source,
                "source_updated_at": acquired_at,
                "source_data": {"display_name": record.display_name},
                "source_data_hash": state_hash,
                "state_version": 1,
                "last_import_job_id": import_job_id,
                "last_import_row_id": import_row_id,
            }
        )
        current_metrics_rows.append(
            {
                "id": _fixture_uuid(f"{prefix}-current-metrics", index),
                "influencer_id": influencer_id,
                "platform_account_id": account_id,
                "source": record.source,
                "source_updated_at": acquired_at,
                "metrics": {"followers_count": index},
                "metrics_hash": metrics_hash,
                "last_import_job_id": import_job_id,
                "last_import_row_id": import_row_id,
            }
        )
        snapshot_rows.append(
            {
                "id": _fixture_uuid(f"{prefix}-snapshot", index),
                "influencer_id": influencer_id,
                "platform_account_id": account_id,
                "source": record.source,
                "source_updated_at": acquired_at,
                "import_job_id": import_job_id,
                "import_row_id": import_row_id,
                "captured_at": acquired_at,
                "metrics": {"followers_count": index},
                "metrics_hash": metrics_hash,
                "snapshot_key": _snapshot_key(prefix, index),
            }
        )
        contact_rows.append(
            {
                "id": _fixture_uuid(f"{prefix}-contact", index),
                "influencer_id": influencer_id,
                "platform_account_id": account_id,
                "type": contact.type,
                "value": contact.value,
                "normalized_value": contact.normalized_value,
                "source": record.source,
                "validation_status": contact.validation_status,
                "is_current": True,
                "possible_duplicate_contact": False,
                "first_seen_at": acquired_at,
                "last_seen_at": acquired_at,
                "source_updated_at": acquired_at,
                "first_import_job_id": import_job_id,
                "first_import_row_id": import_row_id,
                "last_import_job_id": import_job_id,
                "last_import_row_id": import_row_id,
            }
        )

    await _execute_chunks(session, Influencer, influencer_rows)
    await _execute_chunks(session, InfluencerPlatformAccount, account_rows)
    await _execute_chunks(session, ImportRow, import_rows)
    await _execute_chunks(session, PlatformAccountSourceIdentity, source_identity_rows)
    await _execute_chunks(session, InfluencerSourceState, source_state_rows)
    await _execute_chunks(session, InfluencerCurrentMetrics, current_metrics_rows)
    await _execute_chunks(session, InfluencerMetricSnapshot, snapshot_rows)
    await _execute_chunks(session, InfluencerContact, contact_rows)
    await session.commit()


async def _measure[T](
    harness: PostgresHarness,
    *,
    label: str,
    rows: int,
    operation: Callable[[], Awaitable[T]],
) -> tuple[T, SqlMeasurement]:
    harness.probe.start()
    try:
        result = await operation()
    finally:
        measurement = harness.probe.stop(label=label, rows=rows)
    _emit(measurement)
    return result, measurement


def _assert_read_only(measurement: SqlMeasurement) -> None:
    assert measurement.total == measurement.selects
    assert measurement.dml == 0
    assert measurement.advisory == 0
    assert measurement.flushes == 0
    assert measurement.transactions == 1


def _assert_chunk_bound(measurement: SqlMeasurement, expected_selects: int) -> None:
    _assert_read_only(measurement)
    assert measurement.selects == expected_selects


async def _prefetch_and_match(
    session: AsyncSession,
    probe: SqlProbe,
    context: BulkImportContext,
    records: Sequence[CanonicalInfluencerRecord],
    *,
    existing_records: int = 0,
) -> PrefetchedImportState:
    """Run the production prefetch-to-Planner seam in one measured window."""

    state = await BulkImportRepository(session, chunk_size=CHUNK_SIZE).prefetch(context)
    sql_after_prefetch = probe.total
    planner = ImportPlanner(PrefetchedImportRepository(state))
    for index, record in enumerate(records):
        match = await planner.match(record)
        if index < existing_records:
            assert match.account is not None
            assert match.match_type is ImportMatchType.PLATFORM_ACCOUNT_ID
            assert not match.manual_review
        else:
            assert match.account is None
            assert match.match_type is ImportMatchType.NONE
            assert not match.manual_review
    # The all-row Planner pass must remain a pure in-memory consumer of the
    # frozen prefetch state.  Any SQL here reintroduces an N+1 query path.
    assert probe.total == sql_after_prefetch
    return state


def test_reference_match_before_baseline_is_linear_on_postgresql_16() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            for row_count in (50, 500, 2_000):
                records = _records(row_count, prefix=f"before-{row_count}")
                async with harness.factory() as session:
                    planner = ImportPlanner(ImportRepository(session))

                    async def reference_match(
                        current_records: tuple[CanonicalInfluencerRecord, ...] = records,
                        current_planner: ImportPlanner = planner,
                    ) -> None:
                        for record in current_records:
                            match = await current_planner.match(record)
                            assert match.account is None
                            assert not match.manual_review

                    _, measurement = await _measure(
                        harness,
                        label="before-reference-match",
                        rows=row_count,
                        operation=reference_match,
                    )

                # Three hard identities are queried once per incoming row.
                assert measurement.total == row_count * 3
                assert measurement.selects == row_count * 3
                assert measurement.dml == 0
                assert measurement.advisory == 0
                assert measurement.flushes == 0
                assert measurement.transactions == 1

    asyncio.run(scenario())


def test_integrated_prefetch_and_planner_match_adds_no_per_row_sql() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            for row_count in (50, 500, 2_000):
                prefix = f"integrated-after-{row_count}"
                records = _records(row_count, prefix=prefix)
                context = BulkImportContext.from_records(
                    records,
                    snapshot_keys=_snapshot_keys(prefix, row_count),
                )
                async with harness.factory() as session:
                    state, measurement = await _measure(
                        harness,
                        label="after-integrated-prefetch-and-match",
                        rows=row_count,
                        operation=lambda current_records=records, current_context=context: (
                            _prefetch_and_match(
                                session,
                                harness.probe,
                                current_context,
                                current_records,
                            )
                        ),
                    )
                chunks = (row_count + CHUNK_SIZE - 1) // CHUNK_SIZE
                # All SQL belongs to the five prefetch query families.  The
                # subsequent 50/500/2k Planner match calls add exactly zero.
                _assert_chunk_bound(measurement, 5 * chunks)
                assert not state.accounts_by_id
                assert not state.influencers_by_id

    asyncio.run(scenario())


def test_bulk_prefetch_query_count_scales_by_unique_chunks_and_covers_data_mix() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            async with harness.factory() as seed_session:
                await _seed_existing_graph(seed_session, 2_000, prefix="existing")

            for row_count in (50, 500, 2_000):
                records = _records(row_count, prefix=f"new-{row_count}")
                context = BulkImportContext.from_records(
                    records,
                    snapshot_keys=_snapshot_keys(f"new-{row_count}", row_count),
                )
                async with harness.factory() as session:
                    state, measurement = await _measure(
                        harness,
                        label="after-all-new-prefetch",
                        rows=row_count,
                        operation=lambda current_context=context: BulkImportRepository(
                            session, chunk_size=CHUNK_SIZE
                        ).prefetch(current_context),
                    )
                chunks = (row_count + CHUNK_SIZE - 1) // CHUNK_SIZE
                # platform id + profile URL + external id + snapshot + Contact
                _assert_chunk_bound(measurement, 5 * chunks)
                assert not state.accounts_by_id
                assert not state.influencers_by_id
                assert not state.existing_snapshot_keys

            existing_records = _records(2_000, prefix="existing")
            existing_context = BulkImportContext.from_records(
                existing_records,
                snapshot_keys=_snapshot_keys("existing", 2_000),
            )
            async with harness.factory() as session:
                existing_state, existing_measurement = await _measure(
                    harness,
                    label="after-all-existing-prefetch",
                    rows=2_000,
                    operation=lambda: _prefetch_and_match(
                        session,
                        harness.probe,
                        existing_context,
                        existing_records,
                        existing_records=2_000,
                    ),
                )
            # Three identities, then Influencer, SourceState, CurrentMetrics,
            # Snapshot, source Contact, and normalized Contact queries.
            _assert_chunk_bound(existing_measurement, 9 * 4)
            assert len(existing_state.accounts_by_id) == 2_000
            assert len(existing_state.influencers_by_id) == 2_000
            assert len(existing_state.source_identities) == 2_000
            assert len(existing_state.source_states) == 2_000
            assert len(existing_state.current_metrics) == 2_000
            assert len(existing_state.existing_snapshot_keys) == 2_000
            assert sum(map(len, existing_state.source_contacts.values())) == 2_000
            assert sum(map(len, existing_state.contacts_by_normalized_value.values())) == 2_000

            mixed_records = (
                *_records(1_000, prefix="existing"),
                *_records(1_000, prefix="mixed-new"),
            )
            mixed_snapshots = (
                *_snapshot_keys("existing", 1_000),
                *_snapshot_keys("mixed-new", 1_000),
            )
            mixed_context = BulkImportContext.from_records(
                mixed_records, snapshot_keys=mixed_snapshots
            )
            async with harness.factory() as session:
                mixed_state, mixed_measurement = await _measure(
                    harness,
                    label="after-half-existing-prefetch",
                    rows=2_000,
                    operation=lambda: _prefetch_and_match(
                        session,
                        harness.probe,
                        mixed_context,
                        mixed_records,
                        existing_records=1_000,
                    ),
                )
            # Hard/snapshot/contact query sets have 2k keys (four chunks);
            # account-derived query sets have 1k matched keys (two chunks).
            _assert_chunk_bound(mixed_measurement, (3 * 4) + (4 * 2) + 4 + 4)
            assert len(mixed_state.accounts_by_id) == 1_000
            assert len(mixed_state.influencers_by_id) == 1_000
            assert len(mixed_state.existing_snapshot_keys) == 1_000

            duplicate_records = _records(
                2_000,
                prefix="duplicate-new",
                identity_modulo=20,
                email_modulo=20,
            )
            duplicate_context = BulkImportContext.from_records(
                duplicate_records,
                snapshot_keys=_snapshot_keys("duplicate-new", 20),
            )
            async with harness.factory() as session:
                duplicate_state, duplicate_measurement = await _measure(
                    harness,
                    label="after-duplicate-heavy-prefetch",
                    rows=2_000,
                    operation=lambda: BulkImportRepository(session, chunk_size=CHUNK_SIZE).prefetch(
                        duplicate_context
                    ),
                )
            _assert_chunk_bound(duplicate_measurement, 5)
            assert len(duplicate_context.platform_account_ids) == 20
            assert len(duplicate_context.contact_values) == 20
            assert not duplicate_state.accounts_by_id

            contact_records = _records(
                2_000,
                prefix="contact-heavy-new",
                email_modulo=5,
            )
            contact_context = BulkImportContext.from_records(
                contact_records,
                snapshot_keys=_snapshot_keys("contact-heavy-new", 2_000),
            )
            async with harness.factory() as session:
                contact_state, contact_measurement = await _measure(
                    harness,
                    label="after-contact-heavy-prefetch",
                    rows=2_000,
                    operation=lambda: BulkImportRepository(session, chunk_size=CHUNK_SIZE).prefetch(
                        contact_context
                    ),
                )
            _assert_chunk_bound(contact_measurement, (3 * 4) + 4 + 1)
            assert len(contact_context.contact_values) == 5
            assert sum(map(len, contact_state.contacts_by_normalized_value.values())) == 0

            snapshot_records = _records(
                2_000,
                prefix="snapshot-heavy-new",
                identity_modulo=1,
                email_modulo=1,
            )
            snapshot_context = BulkImportContext.from_records(
                snapshot_records,
                snapshot_keys=_snapshot_keys("snapshot-heavy-new", 2_000),
            )
            async with harness.factory() as session:
                snapshot_state, snapshot_measurement = await _measure(
                    harness,
                    label="after-snapshot-heavy-prefetch",
                    rows=2_000,
                    operation=lambda: BulkImportRepository(session, chunk_size=CHUNK_SIZE).prefetch(
                        snapshot_context
                    ),
                )
            _assert_chunk_bound(snapshot_measurement, 3 + 4 + 1)
            assert len(snapshot_context.platform_account_ids) == 1
            assert len(snapshot_context.snapshot_keys) == 2_000
            assert not snapshot_state.existing_snapshot_keys

    asyncio.run(scenario())


def test_bulk_prefetch_5k_and_10k_capacity_gate() -> None:
    if os.environ.get(PERFORMANCE_ENV) != "1":
        pytest.skip(f"set {PERFORMANCE_ENV}=1 for explicit 5k/10k capacity and soak gates")

    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            for row_count in (5_000, 10_000):
                prefix = f"capacity-{row_count}"
                context_started = perf_counter()
                records = _records(row_count, prefix=prefix)
                context = BulkImportContext.from_records(
                    records,
                    snapshot_keys=_snapshot_keys(prefix, row_count),
                )
                context_wall_seconds = perf_counter() - context_started
                async with harness.factory() as session:
                    state, measurement = await _measure(
                        harness,
                        label="after-capacity-prefetch",
                        rows=row_count,
                        operation=lambda current_context=context: BulkImportRepository(
                            session, chunk_size=CHUNK_SIZE
                        ).prefetch(current_context),
                    )
                chunks = (row_count + CHUNK_SIZE - 1) // CHUNK_SIZE
                _assert_chunk_bound(measurement, 5 * chunks)
                assert len(context.platform_account_ids) == row_count
                assert len(context.snapshot_keys) == row_count
                assert not state.accounts_by_id
                print(
                    "TASK4_CONTEXT "
                    + json.dumps(
                        {
                            "rows": row_count,
                            "wall_seconds": round(context_wall_seconds, 6),
                        },
                        sort_keys=True,
                    )
                )

    asyncio.run(scenario())


def _plan_nodes(plan: Any) -> Iterable[dict[str, Any]]:
    if isinstance(plan, list):
        for item in plan:
            yield from _plan_nodes(item)
    elif isinstance(plan, dict):
        if "Node Type" in plan:
            yield plan
        for value in plan.values():
            yield from _plan_nodes(value)


def _coerce_explain_document(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def test_selective_prefetch_queries_have_expected_postgresql_indexes() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            async with harness.factory() as session:
                await _seed_existing_graph(session, 2_000, prefix="explain")
                await session.execute(text("ANALYZE"))
                await session.commit()

            checks = (
                (
                    "influencer-by-id",
                    "influencers_pkey",
                    """
                    SELECT * FROM influencers
                    WHERE id = :influencer_id
                    """,
                    {"influencer_id": _fixture_uuid("explain-influencer", 0)},
                ),
                (
                    "account-platform-id",
                    "uq_platform_account_identity",
                    """
                    SELECT * FROM influencer_platform_accounts
                    WHERE platform = :platform AND platform_account_id = :value
                    """,
                    {"platform": Platform.XIAOHONGSHU.value, "value": "explain-account-0"},
                ),
                (
                    "account-profile-url",
                    "uq_platform_profile_url",
                    """
                    SELECT * FROM influencer_platform_accounts
                    WHERE platform = :platform AND normalized_profile_url = :value
                    """,
                    {
                        "platform": Platform.XIAOHONGSHU.value,
                        "value": "https://example.invalid/explain/profile/0",
                    },
                ),
                (
                    "source-external-id",
                    "uq_source_platform_external_account",
                    """
                    SELECT account.*
                    FROM platform_account_source_identities AS identity
                    JOIN influencer_platform_accounts AS account
                      ON account.id = identity.platform_account_id
                    WHERE identity.source = :source
                      AND identity.platform = :platform
                      AND identity.external_account_id = :value
                    """,
                    {
                        "source": DataSource.HUITUN.value,
                        "platform": Platform.XIAOHONGSHU.value,
                        "value": "explain-external-0",
                    },
                ),
                (
                    "source-state",
                    "uq_platform_account_source_state",
                    """
                    SELECT * FROM influencer_source_states
                    WHERE platform_account_id = :account_id AND source = :source
                    """,
                    {
                        "account_id": _fixture_uuid("explain-account", 0),
                        "source": DataSource.HUITUN.value,
                    },
                ),
                (
                    "current-metrics",
                    "uq_platform_account_current_metrics",
                    """
                    SELECT * FROM influencer_current_metrics
                    WHERE platform_account_id = :account_id AND source = :source
                    """,
                    {
                        "account_id": _fixture_uuid("explain-account", 0),
                        "source": DataSource.HUITUN.value,
                    },
                ),
                (
                    "snapshot-key",
                    "uq_metric_snapshot_key",
                    """
                    SELECT snapshot_key FROM influencer_metric_snapshots
                    WHERE snapshot_key = :snapshot_key
                    """,
                    {"snapshot_key": _snapshot_key("explain", 0)},
                ),
                (
                    "contact-value",
                    "ix_influencer_contacts_normalized",
                    """
                    SELECT * FROM influencer_contacts
                    WHERE type = :contact_type AND normalized_value = :value
                    """,
                    {
                        "contact_type": ContactType.EMAIL.value,
                        "value": "explain-contact-0@example.invalid",
                    },
                ),
                (
                    "source-contact",
                    "uq_influencer_contact_source",
                    """
                    SELECT * FROM influencer_contacts
                    WHERE influencer_id = :influencer_id
                      AND source = :source
                      AND type = :contact_type
                    """,
                    {
                        "influencer_id": _fixture_uuid("explain-influencer", 0),
                        "source": DataSource.HUITUN.value,
                        "contact_type": ContactType.EMAIL.value,
                    },
                ),
            )
            relevant_relations = {
                "influencers",
                "influencer_platform_accounts",
                "platform_account_source_identities",
                "influencer_source_states",
                "influencer_current_metrics",
                "influencer_metric_snapshots",
                "influencer_contacts",
            }
            async with harness.factory() as session:
                for label, expected_index, query, parameters in checks:
                    value = await session.scalar(
                        text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}"), parameters
                    )
                    document = _coerce_explain_document(value)
                    nodes = tuple(_plan_nodes(document))
                    index_names = {
                        str(node["Index Name"]) for node in nodes if "Index Name" in node
                    }
                    sequential_relations = {
                        str(node.get("Relation Name"))
                        for node in nodes
                        if node.get("Node Type") == "Seq Scan"
                    }
                    print(
                        "TASK4_EXPLAIN "
                        + json.dumps(
                            {
                                "label": label,
                                "indexes": sorted(index_names),
                                "sequential_relations": sorted(sequential_relations),
                            },
                            sort_keys=True,
                        )
                    )
                    assert expected_index in index_names
                    assert not (sequential_relations & relevant_relations)

    asyncio.run(scenario())


def test_bulk_advisory_locks_are_chunked_and_deadlock_safe_on_postgresql_16() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            identities = tuple(f"overlap-identity-{index}" for index in range(1_201))
            reverse_identities = tuple(reversed(identities))
            first_has_locks = asyncio.Event()
            release_first = asyncio.Event()

            async def first_worker() -> tuple[int, ...]:
                async with harness.factory() as session:
                    keys = await BulkImportRepository(
                        session, chunk_size=CHUNK_SIZE
                    ).acquire_identity_locks_bulk(identities)
                    first_has_locks.set()
                    await release_first.wait()
                    await session.commit()
                    return keys

            async def second_worker() -> tuple[int, ...]:
                await first_has_locks.wait()
                async with harness.factory() as session:
                    keys = await BulkImportRepository(
                        session, chunk_size=CHUNK_SIZE
                    ).acquire_identity_locks_bulk(reverse_identities)
                    await session.commit()
                    return keys

            harness.probe.start()
            first_task = asyncio.create_task(first_worker())
            await asyncio.wait_for(first_has_locks.wait(), timeout=10)
            second_task = asyncio.create_task(second_worker())
            done, pending = await asyncio.wait({second_task}, timeout=0.05)
            assert not done
            assert pending == {second_task}
            release_first.set()
            first_keys, second_keys = await asyncio.wait_for(
                asyncio.gather(first_task, second_task), timeout=10
            )
            measurement = harness.probe.stop(label="bulk-advisory-lock-overlap", rows=1_201)
            _emit(measurement)

            assert first_keys == second_keys
            assert first_keys == tuple(sorted(set(first_keys)))
            # 1,201 unique identities / 500 per statement, for two sessions.
            assert measurement.total == 6
            assert measurement.selects == 6
            assert measurement.advisory == 6
            assert measurement.dml == 0
            assert measurement.flushes == 0
            assert measurement.transactions == 2

    asyncio.run(scenario())
