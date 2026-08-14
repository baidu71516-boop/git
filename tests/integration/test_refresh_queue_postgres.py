"""PostgreSQL 16 correctness, concurrency, and 2k gates for refresh queues.

The module only accepts an explicitly named disposable test database.  Every
test creates and drops a random schema, and every fixture uses synthetic public
identity values.  The queue is department-owned while the seeded candidate
library is deliberately company-wide.
"""

from __future__ import annotations

import asyncio
import json
import os
import resource
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any
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
from backend_core.influencers.enums import CRMStage, DataSource, InfluencerStatus, Platform
from backend_core.influencers.freshness import FreshnessPolicy, FreshnessStatus
from backend_core.influencers.models import (
    Influencer,
    InfluencerCurrentMetrics,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)
from backend_core.refresh.enums import (
    RefreshPriorityReason,
    RefreshQueueItemStatus,
    RefreshQueueStatus,
)
from backend_core.refresh.models import RefreshQueue, RefreshQueueItem
from backend_core.refresh.repository import RefreshQueueRepository
from backend_core.refresh.schemas import CriteriaSnapshot, RefreshQueueCreateInput
from backend_core.refresh.service import RefreshQueueService
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema, DropSchema

AS_OF = datetime(2026, 8, 13, 6, 0, tzinfo=UTC)
POLICY_VERSION = 1
MAX_QUEUE_ITEMS = 2_000


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


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


@dataclass(frozen=True, slots=True)
class SqlMeasurement:
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
    """Count queue SQL and retain a qualified process RSS high-water mark."""

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
        if normalized.startswith(("SELECT", "WITH")):
            self.selects += 1
        if normalized.startswith(("INSERT", "UPDATE", "DELETE")):
            self.dml += 1
        if "PG_ADVISORY_XACT_LOCK" in normalized:
            self.advisory += 1

    def start(self) -> None:
        assert not self.active
        self.total = self.selects = self.dml = self.advisory = 0
        self.started_at = perf_counter()
        self.rss_before = _peak_rss_bytes()
        self.active = True

    def stop(self) -> SqlMeasurement:
        assert self.active
        wall_seconds = perf_counter() - self.started_at
        self.active = False
        return SqlMeasurement(
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
    engine: AsyncEngine
    factory: async_sessionmaker[AsyncSession]
    probe: SqlProbe


@asynccontextmanager
async def _isolated_postgres() -> AsyncIterator[PostgresHarness]:
    schema_name = f"phase2_refresh_queue_{uuid4().hex}"
    admin_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    engine: AsyncEngine | None = None
    probe: SqlProbe | None = None
    schema_created = False
    try:
        async with admin_engine.begin() as connection:
            version_number = int(await connection.scalar(text("SHOW server_version_num")))
            assert version_number // 10_000 == 16
            await connection.execute(CreateSchema(schema_name))
        schema_created = True
        engine = create_async_engine(
            TEST_DATABASE_URL,
            connect_args={"options": f"-csearch_path={schema_name}"},
            pool_pre_ping=True,
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        probe = SqlProbe(engine)
        yield PostgresHarness(
            engine=engine,
            factory=async_sessionmaker(engine, expire_on_commit=False),
            probe=probe,
        )
    finally:
        if probe is not None:
            probe.close()
        if engine is not None:
            await engine.dispose()
        try:
            if schema_created:
                async with admin_engine.begin() as connection:
                    await connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
        finally:
            await admin_engine.dispose()


@dataclass(frozen=True, slots=True)
class Actor:
    department_id: UUID
    operator_id: UUID
    auth_session_id: UUID
    role: Role


async def _seed_actor(
    session: AsyncSession,
    *,
    label: str,
    role: Role = Role.OPERATOR,
) -> Actor:
    department = Department(
        name=f"Refresh queue {label} {uuid4().hex}",
        password_hash="not-used-by-refresh-integration-test",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name=f"{label} operator",
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    session.add_all(
        [
            DepartmentPermission(department_id=department.id, role=role),
            operator,
        ]
    )
    await session.flush()
    auth_session = AuthSession(
        department_id=department.id,
        operator_id=operator.id,
        token_hash=uuid4().hex + uuid4().hex,
        csrf_token_hash=uuid4().hex + uuid4().hex,
        ip="127.0.0.1",
        user_agent="refresh-queue-postgres-test",
        expires_at=AS_OF + timedelta(days=1),
        revoked_at=None,
    )
    session.add(auth_session)
    await session.flush()
    return Actor(
        department_id=department.id,
        operator_id=operator.id,
        auth_session_id=auth_session.id,
        role=role,
    )


async def _auth_context(session: AsyncSession, actor: Actor) -> AuthContext:
    department = await session.get(Department, actor.department_id)
    operator = await session.get(Operator, actor.operator_id)
    auth_session = await session.get(AuthSession, actor.auth_session_id)
    assert department is not None
    assert operator is not None
    assert auth_session is not None
    return AuthContext(
        department=department,
        operator=operator,
        role=actor.role,
        auth_session=auth_session,
    )


async def _seed_additional_operator(
    session: AsyncSession,
    actor: Actor,
    *,
    label: str,
) -> Actor:
    operator = Operator(
        department_id=actor.department_id,
        name=f"{label} operator",
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    session.add(operator)
    await session.flush()
    auth_session = AuthSession(
        department_id=actor.department_id,
        operator_id=operator.id,
        token_hash=uuid4().hex + uuid4().hex,
        csrf_token_hash=uuid4().hex + uuid4().hex,
        ip="127.0.0.1",
        user_agent="refresh-queue-postgres-test",
        expires_at=AS_OF + timedelta(days=1),
        revoked_at=None,
    )
    session.add(auth_session)
    await session.flush()
    return Actor(
        department_id=actor.department_id,
        operator_id=operator.id,
        auth_session_id=auth_session.id,
        role=actor.role,
    )


@dataclass(frozen=True, slots=True)
class LineageBundle:
    job_id: UUID
    file_id: UUID


async def _seed_lineage_bundle(
    session: AsyncSession,
    actor: Actor,
    *,
    acquired_at: datetime | None,
    source_type: ImportSourceType = ImportSourceType.MANUAL_HUITUN_EXPORT,
) -> LineageBundle:
    token = uuid4().hex
    collection = CollectionJob(
        id=uuid4(),
        name=f"Refresh lineage {token}",
        industry="synthetic",
        purpose="refresh queue PostgreSQL gate",
        target_action="refresh",
        target_count=MAX_QUEUE_ITEMS,
        department_id=actor.department_id,
        owner_operator_id=actor.operator_id,
        source_type=source_type,
        status=CollectionJobStatus.COMPLETED,
    )
    stored_file = StoredImportFile(
        id=uuid4(),
        sha256=token * 2,
        storage_key=f"task8/{token}.csv",
        size=1,
        detected_type=StoredFileType.CSV,
        detected_mime="text/csv",
        encoding="utf-8",
        expires_at=AS_OF + timedelta(days=30),
    )
    session.add_all([collection, stored_file])
    await session.flush()
    legacy = acquired_at is None
    job = ImportJob(
        id=uuid4(),
        collection_job_id=collection.id,
        department_id=actor.department_id,
        operator_id=actor.operator_id,
        stored_file_id=stored_file.id if legacy else None,
        original_filename=f"{token}.csv" if legacy else None,
        mime_type="text/csv" if legacy else None,
        file_size=1 if legacy else None,
        sha256=stored_file.sha256 if legacy else None,
        source_type=source_type,
        status=ImportJobStatus.COMPLETED,
        preview_revision=1,
        confirmed_revision=1,
        confirmed_at=AS_OF - timedelta(minutes=2),
        completed_at=AS_OF - timedelta(minutes=1),
    )
    session.add(job)
    await session.flush()
    occurrence = ImportJobFile(
        id=uuid4(),
        import_job_id=job.id,
        stored_file_id=stored_file.id,
        position=1,
        original_filename=f"{token}.csv",
        declared_mime="text/csv",
        status=ImportJobFileStatus.READY,
        source_acquired_at=acquired_at,
        source_acquired_at_origin=(
            SourceAcquiredAtOrigin.LEGACY_UNKNOWN
            if acquired_at is None
            else SourceAcquiredAtOrigin.USER_CONFIRMED
        ),
        source_acquired_at_confirmation_required=False,
    )
    session.add(occurrence)
    await session.flush()
    return LineageBundle(job_id=job.id, file_id=occurrence.id)


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    label: str
    observed_at: datetime | None
    followers_count: int | None = 1_000
    source_updated_at: datetime | None = AS_OF - timedelta(days=200)
    eligible_huitun: bool = True
    influencer_status: InfluencerStatus = InfluencerStatus.ACTIVE
    deleted: bool = False
    account_active: bool = True
    platform_account_value: str | None = None
    account_name: str | None = None
    account_handle: str | None = None
    profile_url: str | None = None
    external_ids: tuple[str, ...] = ()
    influencer_id: UUID | None = None
    account_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SeededCandidate:
    spec: CandidateSpec
    influencer_id: UUID
    account_id: UUID
    row_id: UUID


async def _seed_candidates(
    session: AsyncSession,
    actor: Actor,
    specs: Sequence[CandidateSpec],
) -> tuple[SeededCandidate, ...]:
    """Seed candidates in phases so the 2k setup itself does not flush per row."""

    bundle_by_key: dict[tuple[datetime | None, bool], LineageBundle] = {}
    for spec in specs:
        key = (spec.observed_at, spec.eligible_huitun)
        if key not in bundle_by_key:
            bundle_by_key[key] = await _seed_lineage_bundle(
                session,
                actor,
                acquired_at=spec.observed_at,
                source_type=(
                    ImportSourceType.MANUAL_HUITUN_EXPORT
                    if spec.eligible_huitun
                    else ImportSourceType.GENERIC_CSV
                ),
            )

    seeded: list[SeededCandidate] = []
    influencers: list[Influencer] = []
    accounts: list[InfluencerPlatformAccount] = []
    rows: list[ImportRow] = []
    seen_influencer_ids: set[UUID] = set()
    for index, spec in enumerate(specs):
        influencer_id = spec.influencer_id or uuid4()
        account_id = spec.account_id or uuid4()
        row_id = uuid4()
        account_value = (
            spec.platform_account_value
            if spec.platform_account_value is not None
            else f"task8-{spec.label}-{index}"
        )
        account_name = spec.account_name if spec.account_name is not None else spec.label
        account_handle = (
            spec.account_handle if spec.account_handle is not None else f"handle-{spec.label}"
        )
        profile_url = (
            spec.profile_url
            if spec.profile_url is not None
            else f"https://example.invalid/task8/{spec.label}/{index}"
        )
        if influencer_id not in seen_influencer_ids:
            influencers.append(
                Influencer(
                    id=influencer_id,
                    display_name=spec.label,
                    owner_operator_id=actor.operator_id,
                    crm_stage=CRMStage.TO_DEVELOP,
                    status=spec.influencer_status,
                    deleted_at=AS_OF if spec.deleted else None,
                )
            )
            seen_influencer_ids.add(influencer_id)
        accounts.append(
            InfluencerPlatformAccount(
                id=account_id,
                influencer_id=influencer_id,
                platform=Platform.XIAOHONGSHU,
                platform_account_id=account_value,
                account_name=account_name,
                account_handle=account_handle,
                profile_url=profile_url,
                normalized_profile_url=profile_url,
                # Deliberately keep this Huitun even for the negative evidence
                # fixture: account.source alone must never confer eligibility.
                source=DataSource.HUITUN,
                is_active=spec.account_active,
            )
        )
        bundle = bundle_by_key[(spec.observed_at, spec.eligible_huitun)]
        rows.append(
            ImportRow(
                id=row_id,
                import_job_id=bundle.job_id,
                import_job_file_id=bundle.file_id,
                row_number=index + 2,
                raw_data={"fixture": spec.label},
                normalized_data={"fixture": spec.label},
                matched_influencer_id=influencer_id,
                matched_platform_account_id=account_id,
                match_type=ImportMatchType.PLATFORM_ACCOUNT_ID,
                action=ImportRowAction.NO_CHANGE,
                warnings=[],
                errors=[],
                preview_revision=1,
                plan_hash=f"{index + 1:064x}",
                committed_action=ImportRowAction.NO_CHANGE,
                committed_at=AS_OF - timedelta(seconds=index + 1),
            )
        )
        seeded.append(
            SeededCandidate(
                spec=spec,
                influencer_id=influencer_id,
                account_id=account_id,
                row_id=row_id,
            )
        )

    session.add_all(influencers)
    await session.flush()
    session.add_all(accounts)
    await session.flush()
    session.add_all(rows)
    await session.flush()

    source_states: list[InfluencerSourceState] = []
    metrics: list[InfluencerCurrentMetrics] = []
    identities: list[PlatformAccountSourceIdentity] = []
    for index, candidate in enumerate(seeded):
        spec = candidate.spec
        bundle = bundle_by_key[(spec.observed_at, spec.eligible_huitun)]
        source = DataSource.HUITUN if spec.eligible_huitun else DataSource.GENERIC
        source_states.append(
            InfluencerSourceState(
                id=uuid4(),
                influencer_id=candidate.influencer_id,
                platform_account_id=candidate.account_id,
                source=source,
                source_updated_at=spec.source_updated_at,
                source_data={"fixture": spec.label},
                source_data_hash=f"{100_000 + index:064x}",
                state_version=1,
                last_import_job_id=bundle.job_id,
                last_import_row_id=candidate.row_id,
            )
        )
        if spec.followers_count is not None:
            metrics.append(
                InfluencerCurrentMetrics(
                    id=uuid4(),
                    influencer_id=candidate.influencer_id,
                    platform_account_id=candidate.account_id,
                    source=source,
                    source_updated_at=spec.source_updated_at,
                    metrics={"followers_count": spec.followers_count},
                    metrics_hash=f"{200_000 + index:064x}",
                    last_import_job_id=bundle.job_id,
                    last_import_row_id=candidate.row_id,
                )
            )
        for external_id in spec.external_ids:
            identities.append(
                PlatformAccountSourceIdentity(
                    id=uuid4(),
                    platform_account_id=candidate.account_id,
                    platform=Platform.XIAOHONGSHU,
                    source=source,
                    external_account_id=external_id,
                    first_import_job_id=bundle.job_id,
                    first_import_row_id=candidate.row_id,
                    last_import_job_id=bundle.job_id,
                    last_import_row_id=candidate.row_id,
                )
            )
    session.add_all([*source_states, *metrics, *identities])
    await session.commit()
    return tuple(seeded)


def _criteria_snapshot(*, requested_limit: int) -> dict[str, Any]:
    create_input = RefreshQueueCreateInput(
        requested_limit=requested_limit,
        today_total_limit=max(MAX_QUEUE_ITEMS, requested_limit),
        refresh_limit=max(MAX_QUEUE_ITEMS, requested_limit),
    )
    return CriteriaSnapshot.from_policy(
        create_input=create_input,
        policy=FreshnessPolicy(),
    ).model_dump(mode="json")


def _queue(
    actor: Actor,
    *,
    requested_limit: int = 1,
    queue_id: UUID | None = None,
) -> RefreshQueue:
    return RefreshQueue(
        id=queue_id or uuid4(),
        department_id=actor.department_id,
        created_by_operator_id=actor.operator_id,
        status=RefreshQueueStatus.OPEN,
        as_of=AS_OF,
        requested_limit=requested_limit,
        today_total_limit=max(2_000, requested_limit),
        refresh_limit=max(2_000, requested_limit),
        policy_version=POLICY_VERSION,
        criteria_snapshot=_criteria_snapshot(requested_limit=requested_limit),
    )


def _queue_item(
    queue: RefreshQueue,
    candidate: SeededCandidate,
    *,
    status: RefreshQueueItemStatus = RefreshQueueItemStatus.PENDING,
) -> RefreshQueueItem:
    return RefreshQueueItem(
        id=uuid4(),
        department_id=queue.department_id,
        queue_id=queue.id,
        influencer_id=candidate.influencer_id,
        platform_account_id=candidate.account_id,
        source=DataSource.HUITUN,
        priority_tier=2,
        priority_reasons=["VERY_STALE"],
        identity_snapshot={
            "platform": Platform.XIAOHONGSHU.value,
            "account_name": candidate.spec.label,
            "platform_account_id": candidate.spec.platform_account_value,
        },
        baseline_last_observed_at=candidate.spec.observed_at,
        baseline_source_updated_at=candidate.spec.source_updated_at,
        status=status,
    )


async def _count(session: AsyncSession, model: type[object]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


@dataclass(frozen=True, slots=True)
class CreatedQueue:
    queue_id: UUID
    account_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime = AS_OF

    def now(self) -> datetime:
        return self.value


async def _create_queue_service(
    factory: async_sessionmaker[AsyncSession],
    actor: Actor,
    *,
    requested_limit: int,
    start_barrier: asyncio.Barrier | None = None,
) -> CreatedQueue:
    if start_barrier is not None:
        await start_barrier.wait()
    async with factory() as session:
        context = await _auth_context(session, actor)
        detail = await RefreshQueueService(
            session,
            freshness_policy=FreshnessPolicy(),
            clock=FixedClock(),
        ).create_queue(
            context,
            RefreshQueueCreateInput(
                requested_limit=requested_limit,
                today_total_limit=max(requested_limit, MAX_QUEUE_ITEMS),
                refresh_limit=max(requested_limit, MAX_QUEUE_ITEMS),
            ),
            ip="127.0.0.1",
            user_agent="refresh-queue-postgres-test",
        )
        items = await RefreshQueueRepository(session).all_items(detail.queue.id)
        assert detail.queue.as_of == AS_OF
        assert detail.summary.selected == len(items)
        return CreatedQueue(
            queue_id=detail.queue.id,
            account_ids=tuple(item.platform_account_id for item in items),
        )


def test_candidate_priority_identity_baselines_and_eligibility_matrix() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness, harness.factory() as session:
            actor = await _seed_actor(session, label="priority")
            specs = [
                CandidateSpec(
                    label="unknown",
                    observed_at=None,
                    external_ids=("\u3000", "z-source", "a-source"),
                    source_updated_at=AS_OF - timedelta(days=300),
                ),
                CandidateSpec(
                    label="very-stale",
                    observed_at=AS_OF - timedelta(days=120),
                ),
                CandidateSpec(
                    label="stale",
                    observed_at=AS_OF - timedelta(days=60),
                ),
                CandidateSpec(
                    label="aging",
                    observed_at=AS_OF - timedelta(days=15),
                ),
                CandidateSpec(
                    label="fresh-missing-followers",
                    observed_at=AS_OF - timedelta(days=1),
                    followers_count=None,
                ),
                CandidateSpec(
                    label="fresh-complete",
                    observed_at=AS_OF - timedelta(days=1),
                ),
                CandidateSpec(
                    label="no-real-identity",
                    observed_at=None,
                    platform_account_value="\u00a0",
                    account_name="\u3000",
                    account_handle="\u2007",
                    profile_url="\u202f",
                ),
                CandidateSpec(
                    label="inactive-account",
                    observed_at=None,
                    account_active=False,
                ),
                CandidateSpec(
                    label="source-column-is-not-evidence",
                    observed_at=None,
                    eligible_huitun=False,
                ),
                CandidateSpec(
                    label="disabled-influencer",
                    observed_at=None,
                    influencer_status=InfluencerStatus.DISABLED,
                ),
                CandidateSpec(
                    label="deleted-influencer",
                    observed_at=None,
                    deleted=True,
                ),
            ]
            seeded = await _seed_candidates(session, actor, specs)
            seeded_by_label = {item.spec.label: item for item in seeded}

            records = await RefreshQueueRepository(session).list_candidates(
                department_id=actor.department_id,
                as_of=AS_OF,
                policy=FreshnessPolicy.from_day_thresholds(7, 30, 90),
                limit=100,
            )

            assert [record.platform_account_id for record in records] == [
                seeded_by_label[label].account_id
                for label in (
                    "unknown",
                    "very-stale",
                    "stale",
                    "aging",
                    "fresh-missing-followers",
                )
            ]
            assert [record.priority_tier for record in records] == [1, 2, 3, 4, 5]
            assert [record.freshness_status for record in records] == [
                FreshnessStatus.UNKNOWN,
                FreshnessStatus.VERY_STALE,
                FreshnessStatus.STALE,
                FreshnessStatus.AGING,
                FreshnessStatus.FRESH,
            ]
            assert [record.priority_reasons for record in records] == [
                (RefreshPriorityReason.FRESHNESS_UNKNOWN,),
                (RefreshPriorityReason.VERY_STALE,),
                (RefreshPriorityReason.STALE,),
                (RefreshPriorityReason.AGING,),
                (RefreshPriorityReason.FOLLOWERS_MISSING,),
            ]
            unknown = records[0]
            assert unknown.external_source_id == "a-source"
            assert unknown.baseline_source_updated_at == AS_OF - timedelta(days=300)
            assert unknown.last_observed_at is None
            assert unknown.followers_count == 1_000

            excluded_labels = {
                "fresh-complete",
                "no-real-identity",
                "inactive-account",
                "source-column-is-not-evidence",
                "disabled-influencer",
                "deleted-influencer",
            }
            assert not (
                {record.platform_account_id for record in records}
                & {seeded_by_label[label].account_id for label in excluded_labels}
            )

    asyncio.run(scenario())


def test_stable_ties_multi_account_limit_and_company_candidates_match_across_departments() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness, harness.factory() as session:
            first_actor = await _seed_actor(session, label="stable-a")
            second_actor = await _seed_actor(session, label="stable-b")
            shared_influencer_id = UUID(int=10)
            specs = [
                CandidateSpec(
                    label="later-influencer",
                    observed_at=AS_OF - timedelta(days=120),
                    influencer_id=UUID(int=30),
                    account_id=UUID(int=3_003),
                ),
                CandidateSpec(
                    label="shared-account-later",
                    observed_at=AS_OF - timedelta(days=120),
                    influencer_id=shared_influencer_id,
                    account_id=UUID(int=1_009),
                ),
                CandidateSpec(
                    label="shared-account-first",
                    observed_at=AS_OF - timedelta(days=120),
                    influencer_id=shared_influencer_id,
                    account_id=UUID(int=1_002),
                ),
            ]
            await _seed_candidates(session, first_actor, specs)
            expected = [UUID(int=1_002), UUID(int=1_009), UUID(int=3_003)]

            repository = RefreshQueueRepository(session)
            first = await repository.list_candidates(
                department_id=first_actor.department_id,
                as_of=AS_OF,
                policy=FreshnessPolicy(),
                limit=3,
            )
            repeated = await repository.list_candidates(
                department_id=first_actor.department_id,
                as_of=AS_OF,
                policy=FreshnessPolicy(),
                limit=3,
            )
            other_department = await repository.list_candidates(
                department_id=second_actor.department_id,
                as_of=AS_OF,
                policy=FreshnessPolicy(),
                limit=3,
            )
            limited = await repository.list_candidates(
                department_id=first_actor.department_id,
                as_of=AS_OF,
                policy=FreshnessPolicy(),
                limit=2,
            )

            assert [record.platform_account_id for record in first] == expected
            assert [record.platform_account_id for record in repeated] == expected
            assert [record.platform_account_id for record in other_department] == expected
            assert [record.platform_account_id for record in limited] == expected[:2]
            assert [record.influencer_id for record in first[:2]] == [
                shared_influencer_id,
                shared_influencer_id,
            ]

    asyncio.run(scenario())


def test_same_department_concurrent_creation_has_no_overlapping_active_items() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            async with harness.factory() as session:
                first_actor = await _seed_actor(session, label="concurrent")
                second_actor = await _seed_additional_operator(
                    session,
                    first_actor,
                    label="concurrent-second",
                )
                await session.commit()
                specs = [
                    CandidateSpec(
                        label=f"overlap-{index:02d}",
                        observed_at=AS_OF - timedelta(days=120),
                        influencer_id=UUID(int=index + 1),
                        account_id=UUID(int=50_000 + index + 1),
                    )
                    for index in range(40)
                ]
                await _seed_candidates(session, first_actor, specs)

            barrier = asyncio.Barrier(3)
            tasks = [
                asyncio.create_task(
                    _create_queue_service(
                        harness.factory,
                        actor,
                        requested_limit=30,
                        start_barrier=barrier,
                    )
                )
                for actor in (first_actor, second_actor)
            ]
            await barrier.wait()
            first, second = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)

            assert sorted((len(first.account_ids), len(second.account_ids))) == [10, 30]
            assert not (set(first.account_ids) & set(second.account_ids))
            assert len(set(first.account_ids) | set(second.account_ids)) == 40
            async with harness.factory() as session:
                duplicate_count = int(
                    await session.scalar(
                        text(
                            """
                            SELECT count(*)
                            FROM (
                                SELECT platform_account_id, source, count(*)
                                FROM refresh_queue_items
                                WHERE department_id = :department_id
                                  AND status IN ('pending', 'stale_return', 'unresolved')
                                GROUP BY platform_account_id, source
                                HAVING count(*) > 1
                            ) AS duplicates
                            """
                        ),
                        {"department_id": first_actor.department_id},
                    )
                    or 0
                )
                assert duplicate_count == 0
                assert await _count(session, RefreshQueue) == 2
                assert await _count(session, RefreshQueueItem) == 40
                assert (
                    int(
                        await session.scalar(
                            select(func.count(AuditLog.id)).where(
                                AuditLog.action == AuditAction.REFRESH_QUEUE_CREATED
                            )
                        )
                        or 0
                    )
                    == 2
                )

    asyncio.run(scenario())


def test_queue_and_items_roll_back_atomically_when_item_insert_fails() -> None:
    class InjectedItemFailure(RuntimeError):
        pass

    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            async with harness.factory() as session:
                actor = await _seed_actor(session, label="rollback")
                await session.commit()
                await _seed_candidates(
                    session,
                    actor,
                    [
                        CandidateSpec(
                            label=f"rollback-{index}",
                            observed_at=AS_OF - timedelta(days=120),
                        )
                        for index in range(3)
                    ],
                )

            def fail_item_insert(
                _connection: object,
                _cursor: object,
                statement: str,
                _parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                if statement.lstrip().upper().startswith("INSERT INTO REFRESH_QUEUE_ITEMS"):
                    raise InjectedItemFailure("synthetic queue item persistence failure")

            event.listen(harness.engine.sync_engine, "before_cursor_execute", fail_item_insert)
            try:
                with pytest.raises(InjectedItemFailure):
                    await _create_queue_service(
                        harness.factory,
                        actor,
                        requested_limit=3,
                    )
            finally:
                event.remove(
                    harness.engine.sync_engine,
                    "before_cursor_execute",
                    fail_item_insert,
                )

            async with harness.factory() as session:
                assert await _count(session, RefreshQueue) == 0
                assert await _count(session, RefreshQueueItem) == 0
                assert await _count(session, AuditLog) == 0
                candidates = await RefreshQueueRepository(session).list_candidates(
                    department_id=actor.department_id,
                    as_of=AS_OF,
                    policy=FreshnessPolicy(),
                    limit=10,
                )
                assert len(candidates) == 3

    asyncio.run(scenario())


def test_freshness_writer_race_yields_one_statement_consistent_priority_and_baselines() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            async with harness.factory() as session:
                actor = await _seed_actor(session, label="freshness-race")
                second_actor = await _seed_actor(session, label="freshness-race-second")
                old_observed_at = AS_OF - timedelta(days=120)
                old_source_updated_at = AS_OF - timedelta(days=150)
                candidate = (
                    await _seed_candidates(
                        session,
                        actor,
                        [
                            CandidateSpec(
                                label="freshness-race-candidate",
                                observed_at=old_observed_at,
                                followers_count=None,
                                source_updated_at=old_source_updated_at,
                            )
                        ],
                    )
                )[0]

            writer_ready = asyncio.Event()
            reader_done = asyncio.Event()
            new_observed_at = AS_OF - timedelta(days=1)
            new_source_updated_at = AS_OF

            async def writer() -> None:
                async with harness.factory() as session, session.begin():
                    row = await session.get(ImportRow, candidate.row_id)
                    assert row is not None
                    occurrence = await session.get(ImportJobFile, row.import_job_file_id)
                    source_state = await session.scalar(
                        select(InfluencerSourceState).where(
                            InfluencerSourceState.platform_account_id == candidate.account_id,
                            InfluencerSourceState.source == DataSource.HUITUN,
                        )
                    )
                    assert occurrence is not None
                    assert source_state is not None
                    occurrence.source_acquired_at = new_observed_at
                    source_state.source_updated_at = new_source_updated_at
                    await session.flush()
                    writer_ready.set()
                    await asyncio.wait_for(reader_done.wait(), timeout=10)

            writer_task = asyncio.create_task(writer())
            await asyncio.wait_for(writer_ready.wait(), timeout=10)
            try:
                created = await _create_queue_service(
                    harness.factory,
                    actor,
                    requested_limit=1,
                )
                async with harness.factory() as session:
                    during_write = await session.scalar(
                        select(RefreshQueueItem).where(
                            RefreshQueueItem.queue_id == created.queue_id
                        )
                    )
                assert during_write is not None
                assert during_write.priority_tier == 2
                assert during_write.priority_reasons == [
                    RefreshPriorityReason.VERY_STALE.value,
                    RefreshPriorityReason.FOLLOWERS_MISSING.value,
                ]
                assert during_write.baseline_last_observed_at == old_observed_at
                assert during_write.baseline_source_updated_at == old_source_updated_at
            finally:
                reader_done.set()
                await asyncio.wait_for(writer_task, timeout=10)

            after_commit = await _create_queue_service(
                harness.factory,
                second_actor,
                requested_limit=1,
            )
            async with harness.factory() as session:
                refreshed = await session.scalar(
                    select(RefreshQueueItem).where(
                        RefreshQueueItem.queue_id == after_commit.queue_id
                    )
                )
            assert refreshed is not None
            assert refreshed.platform_account_id == during_write.platform_account_id
            assert during_write.department_id == actor.department_id
            assert refreshed.department_id == second_actor.department_id
            assert refreshed.department_id != during_write.department_id
            assert refreshed.priority_tier == 5
            assert refreshed.priority_reasons == [RefreshPriorityReason.FOLLOWERS_MISSING.value]
            assert refreshed.baseline_last_observed_at == new_observed_at
            assert refreshed.baseline_source_updated_at == new_source_updated_at

    asyncio.run(scenario())


def test_2000_candidate_selection_persistence_pagination_summary_and_resource_gate() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            async with harness.factory() as session:
                actor = await _seed_actor(session, label="2k")
                specs = [
                    CandidateSpec(
                        label=f"scale-{index:04d}",
                        observed_at=AS_OF - timedelta(days=120),
                        influencer_id=UUID(int=index + 1),
                        account_id=UUID(int=100_000 + index + 1),
                    )
                    for index in range(MAX_QUEUE_ITEMS)
                ]
                await _seed_candidates(session, actor, specs)

                repository = RefreshQueueRepository(session)
                harness.probe.start()
                candidates = await repository.list_candidates(
                    department_id=actor.department_id,
                    as_of=AS_OF,
                    policy=FreshnessPolicy(),
                    limit=MAX_QUEUE_ITEMS,
                )
                selection_measurement = harness.probe.stop()
                assert len(candidates) == MAX_QUEUE_ITEMS
                assert selection_measurement.total == 1
                assert selection_measurement.selects == 1
                assert selection_measurement.dml == 0
                assert [record.influencer_id for record in candidates] == [
                    UUID(int=index + 1) for index in range(MAX_QUEUE_ITEMS)
                ]

            harness.probe.start()
            created = await _create_queue_service(
                harness.factory,
                actor,
                requested_limit=MAX_QUEUE_ITEMS,
            )
            creation_measurement = harness.probe.stop()
            assert len(created.account_ids) == MAX_QUEUE_ITEMS
            assert list(created.account_ids) == [
                UUID(int=100_000 + index + 1) for index in range(MAX_QUEUE_ITEMS)
            ]

            async with harness.factory() as session:
                repository = RefreshQueueRepository(session)
                queue = await repository.get_queue(
                    created.queue_id,
                    department_id=actor.department_id,
                )
                assert queue is not None
                assert queue.requested_limit == MAX_QUEUE_ITEMS
                assert queue.as_of == AS_OF

                first_page, first_total = await repository.list_items(
                    queue.id,
                    offset=0,
                    limit=200,
                )
                second_page, second_total = await repository.list_items(
                    queue.id,
                    offset=200,
                    limit=200,
                )
                summary = await repository.summary(queue.id)
                assert first_total == second_total == MAX_QUEUE_ITEMS
                assert len(first_page) == len(second_page) == 200
                assert not ({item.id for item in first_page} & {item.id for item in second_page})
                assert [item.influencer_id for item in first_page] == [
                    UUID(int=index + 1) for index in range(200)
                ]
                assert [item.influencer_id for item in second_page] == [
                    UUID(int=index + 1) for index in range(200, 400)
                ]
                assert summary.selected == MAX_QUEUE_ITEMS
                assert summary.unique_influencers == MAX_QUEUE_ITEMS
                assert summary.freshness_breakdown == {
                    FreshnessStatus.VERY_STALE.value: MAX_QUEUE_ITEMS
                }
                assert summary.priority_breakdown == {2: MAX_QUEUE_ITEMS}
                assert summary.status_breakdown == {
                    RefreshQueueItemStatus.PENDING.value: MAX_QUEUE_ITEMS
                }
                assert (
                    int(
                        await session.scalar(
                            select(func.count(AuditLog.id)).where(
                                AuditLog.action == AuditAction.REFRESH_QUEUE_CREATED
                            )
                        )
                        or 0
                    )
                    == 1
                )

            total_wall = selection_measurement.wall_seconds + creation_measurement.wall_seconds
            peak_rss = max(
                selection_measurement.rss_high_water_after_bytes,
                creation_measurement.rss_high_water_after_bytes,
            )
            observation = {
                "rows": MAX_QUEUE_ITEMS,
                "selection_sql": selection_measurement.total,
                "creation_sql": creation_measurement.total,
                "creation_dml": creation_measurement.dml,
                "advisory_lock_sql": creation_measurement.advisory,
                "wall_seconds": round(total_wall, 6),
                "rss_high_water_bytes": peak_rss,
                "rss_high_water_delta_bytes": max(
                    selection_measurement.rss_high_water_delta_bytes,
                    creation_measurement.rss_high_water_delta_bytes,
                ),
            }
            print("TASK8_REFRESH_QUEUE_2000 " + json.dumps(observation, sort_keys=True))
            assert creation_measurement.advisory == 2
            assert 0 < creation_measurement.dml <= 5
            assert creation_measurement.total < 30
            assert total_wall < 60
            assert peak_rss < 900 * 1024 * 1024

    asyncio.run(scenario())


def test_active_candidate_constraint_is_department_scoped_and_released_by_terminal_item() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness, harness.factory() as session:
            first_actor = await _seed_actor(session, label="first")
            second_actor = await _seed_actor(session, label="second")
            candidate = (
                await _seed_candidates(
                    session,
                    first_actor,
                    [
                        CandidateSpec(
                            label="shared-company-candidate",
                            observed_at=AS_OF - timedelta(days=120),
                        )
                    ],
                )
            )[0]

            first_queue = _queue(first_actor)
            session.add(first_queue)
            await session.flush()
            first_item = _queue_item(first_queue, candidate)
            session.add(first_item)
            await session.commit()
            first_item_id = first_item.id

            first_department_candidates = await RefreshQueueRepository(session).list_candidates(
                department_id=first_actor.department_id,
                as_of=AS_OF,
                policy=FreshnessPolicy(),
                limit=10,
            )
            second_department_candidates = await RefreshQueueRepository(session).list_candidates(
                department_id=second_actor.department_id,
                as_of=AS_OF,
                policy=FreshnessPolicy(),
                limit=10,
            )
            assert first_department_candidates == []
            assert [item.platform_account_id for item in second_department_candidates] == [
                candidate.account_id
            ]

            conflicting_queue = _queue(first_actor)
            session.add(conflicting_queue)
            await session.flush()
            session.add(_queue_item(conflicting_queue, candidate))
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()

            other_department_queue = _queue(second_actor)
            session.add(other_department_queue)
            await session.flush()
            session.add(_queue_item(other_department_queue, candidate))
            await session.commit()

            persisted_first_item = await session.get(RefreshQueueItem, first_item_id)
            assert persisted_first_item is not None
            persisted_first_item.status = RefreshQueueItemStatus.CANCELLED
            await session.commit()

            replacement_queue = _queue(first_actor)
            session.add(replacement_queue)
            await session.flush()
            session.add(_queue_item(replacement_queue, candidate))
            await session.commit()

            assert await _count(session, RefreshQueue) == 3
            assert await _count(session, RefreshQueueItem) == 3

    asyncio.run(scenario())
