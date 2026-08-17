"""PostgreSQL query-shape coverage for deterministic Candidate Pool materialization."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.auth.service import AuthContext
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.growth.enums import Phase3AOperationScope
from backend_core.growth.models import (
    CandidatePool,
    Phase3AIdempotencyRecord,
    TargetingPolicy,
)
from backend_core.growth.repository import CandidatePoolRepository
from backend_core.growth.schemas import (
    CandidatePoolCreateInput,
    CandidatePoolPublic,
    TargetingPolicyCreateInput,
    TargetingPolicyCreateResultPublic,
)
from backend_core.growth.service import CandidatePoolService
from backend_core.growth.targeting import IntegerRange, SellerTargetingPolicy
from backend_core.influencers.enums import CRMStage, DataSource, InfluencerStatus, Platform
from backend_core.influencers.freshness import FreshnessPolicy
from backend_core.influencers.models import Influencer, InfluencerPlatformAccount
from sqlalchemy import event, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema, DropSchema

ACCOUNT_COUNT = 501
MAX_MATERIALIZATION_SELECTS = 16


def _test_database_url() -> URL:
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
            "TEST_DATABASE_URL must be an explicitly named PostgreSQL test database",
            pytrace=False,
        )
    return url.set(drivername="postgresql+psycopg")


TEST_DATABASE_URL = _test_database_url()


class SqlSelectProbe:
    """Count read statements issued by a materialization run."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.active = False
        self.selects = 0
        event.listen(engine.sync_engine, "before_cursor_execute", self._before_execute)

    def _before_execute(
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
        if normalized.startswith(("SELECT", "WITH")):
            self.selects += 1

    def start(self) -> None:
        assert not self.active
        self.selects = 0
        self.active = True

    def stop(self) -> int:
        assert self.active
        self.active = False
        return self.selects

    def close(self) -> None:
        event.remove(self.engine.sync_engine, "before_cursor_execute", self._before_execute)


@dataclass(frozen=True, slots=True)
class PostgresHarness:
    factory: async_sessionmaker[AsyncSession]
    probe: SqlSelectProbe


@dataclass(slots=True)
class SharedRecordLookupRace:
    """Coordinate only the two initial empty shared-record lookups."""

    barrier: asyncio.Barrier
    empty_lookup_count: int = 0


@asynccontextmanager
async def _isolated_postgres() -> AsyncIterator[PostgresHarness]:
    schema_name = f"candidate_pool_targeting_{uuid4().hex}"
    admin_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    test_engine: AsyncEngine | None = None
    probe: SqlSelectProbe | None = None
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
        probe = SqlSelectProbe(test_engine)
        yield PostgresHarness(
            factory=async_sessionmaker(test_engine, expire_on_commit=False),
            probe=probe,
        )
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


async def _actor(session: AsyncSession) -> AuthContext:
    department = Department(
        name=f"Candidate targeting performance {uuid4().hex}",
        password_hash="not-used-by-test",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name="Candidate targeting performance operator",
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    session.add_all(
        (
            operator,
            DepartmentPermission(department_id=department.id, role=Role.OPERATOR),
        )
    )
    await session.flush()
    auth_session = AuthSession(
        department_id=department.id,
        operator_id=operator.id,
        token_hash=uuid4().hex * 2,
        csrf_token_hash=uuid4().hex * 2,
        ip="192.0.2.44",
        user_agent="candidate-pool-postgres-test",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        revoked_at=None,
    )
    session.add(auth_session)
    await session.flush()
    return AuthContext(
        department=department,
        operator=operator,
        role=Role.OPERATOR,
        auth_session=auth_session,
    )


async def _seed_accounts(session: AsyncSession, *, owner_id: UUID) -> None:
    influencers = [
        Influencer(
            display_name=f"Candidate targeting {index}",
            owner_operator_id=owner_id,
            crm_stage=CRMStage.TO_DEVELOP,
            status=InfluencerStatus.ACTIVE,
            deleted_at=None,
        )
        for index in range(ACCOUNT_COUNT)
    ]
    session.add_all(influencers)
    await session.flush()
    session.add_all(
        (
            InfluencerPlatformAccount(
                influencer_id=influencer.id,
                platform=Platform.XIAOHONGSHU,
                platform_account_id=f"candidate-targeting-{index}",
                account_name=f"Candidate targeting {index}",
                account_handle=f"candidate-targeting-{index}",
                profile_url=f"https://example.invalid/candidate-targeting/{index}",
                normalized_profile_url=f"https://example.invalid/candidate-targeting/{index}",
                source=DataSource.GENERIC,
                source_tags=["beauty"],
                is_active=True,
            )
            for index, influencer in enumerate(influencers)
        )
    )
    await session.flush()


def _force_shared_record_lookup_race(
    monkeypatch: pytest.MonkeyPatch,
    *,
    operation_scope: Phase3AOperationScope,
    idempotency_keys: frozenset[str],
) -> SharedRecordLookupRace:
    """Make both callers observe an absent shared record before either inserts."""

    race = SharedRecordLookupRace(barrier=asyncio.Barrier(2))
    original_lookup = CandidatePoolRepository.get_phase3a_idempotency_record

    async def synchronized_lookup(
        repository: CandidatePoolRepository,
        *,
        department_id: UUID,
        operation_scope: Phase3AOperationScope,
        idempotency_key: str,
    ) -> Phase3AIdempotencyRecord | None:
        record = await original_lookup(
            repository,
            department_id=department_id,
            operation_scope=operation_scope,
            idempotency_key=idempotency_key,
        )
        if (
            record is None
            and operation_scope == race_operation_scope
            and idempotency_key in race_idempotency_keys
            and race.empty_lookup_count < 2
        ):
            race.empty_lookup_count += 1
            await race.barrier.wait()
        return record

    race_operation_scope = operation_scope
    race_idempotency_keys = idempotency_keys
    monkeypatch.setattr(
        CandidatePoolRepository,
        "get_phase3a_idempotency_record",
        synchronized_lookup,
    )
    return race


async def _model_count(session: AsyncSession, model: type[object]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _audit_count(
    session: AsyncSession,
    *,
    action: AuditAction,
    entity_id: UUID,
) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == action, AuditLog.entity_id == entity_id)
        )
        or 0
    )


def test_concurrent_pool_create_key_replays_the_single_persisted_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            async with harness.factory() as session:
                context = await _actor(session)
                await session.commit()

            idempotency_key = "postgres-pool-create-race"
            payload = CandidatePoolCreateInput(
                name="PostgreSQL same-key pool creation",
                kind="POTENTIAL_SELLER",
                policy=SellerTargetingPolicy(tags_exact_any=("beauty",)),
            )
            lookup_race = _force_shared_record_lookup_race(
                monkeypatch,
                operation_scope=Phase3AOperationScope.CANDIDATE_POOL_CREATE,
                idempotency_keys=frozenset((idempotency_key,)),
            )
            start_barrier = asyncio.Barrier(3)

            async def create_pool() -> CandidatePoolPublic:
                await start_barrier.wait()
                async with harness.factory() as session:
                    return await CandidatePoolService(
                        session,
                        freshness_policy=FreshnessPolicy(),
                    ).create_pool(
                        context,
                        payload,
                        idempotency_key=idempotency_key,
                        ip="192.0.2.44",
                        user_agent="candidate-pool-postgres-race-test",
                    )

            tasks = [asyncio.create_task(create_pool()) for _ in range(2)]
            await start_barrier.wait()
            first, replay = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)

            assert lookup_race.empty_lookup_count == 2
            assert first.model_dump(mode="json") == replay.model_dump(mode="json")

            async with harness.factory() as session:
                record = await session.scalar(
                    select(Phase3AIdempotencyRecord).where(
                        Phase3AIdempotencyRecord.department_id == context.department.id,
                        Phase3AIdempotencyRecord.operation_scope
                        == Phase3AOperationScope.CANDIDATE_POOL_CREATE,
                        Phase3AIdempotencyRecord.idempotency_key == idempotency_key,
                    )
                )
                assert record is not None
                assert record.result_entity_id == first.id
                assert await _model_count(session, CandidatePool) == 1
                assert await _model_count(session, TargetingPolicy) == 1
                assert await _model_count(session, Phase3AIdempotencyRecord) == 1
                assert (
                    await _audit_count(
                        session,
                        action=AuditAction.CANDIDATE_POOL_CREATED,
                        entity_id=first.id,
                    )
                ) == 1
                assert first.current_policy_id is not None
                assert (
                    await _audit_count(
                        session,
                        action=AuditAction.TARGETING_POLICY_CREATED,
                        entity_id=first.current_policy_id,
                    )
                ) == 1

    asyncio.run(scenario())


def test_concurrent_policy_create_key_replays_the_single_persisted_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            async with harness.factory() as setup_session:
                context = await _actor(setup_session)
                pool = await CandidatePoolService(
                    setup_session,
                    freshness_policy=FreshnessPolicy(),
                ).create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="PostgreSQL policy race pool",
                        kind="POTENTIAL_SELLER",
                        policy=SellerTargetingPolicy(tags_exact_any=("beauty",)),
                    ),
                    idempotency_key="postgres-policy-race-bootstrap",
                    ip="192.0.2.44",
                    user_agent="candidate-pool-postgres-race-test",
                )

            idempotency_key = "postgres-policy-create-race"
            payload = TargetingPolicyCreateInput(
                policy=SellerTargetingPolicy(followers=IntegerRange(minimum=100))
            )
            lookup_race = _force_shared_record_lookup_race(
                monkeypatch,
                operation_scope=Phase3AOperationScope.TARGETING_POLICY_CREATE,
                idempotency_keys=frozenset((idempotency_key,)),
            )
            start_barrier = asyncio.Barrier(3)

            async def append_policy() -> TargetingPolicyCreateResultPublic:
                await start_barrier.wait()
                async with harness.factory() as session:
                    return await CandidatePoolService(
                        session,
                        freshness_policy=FreshnessPolicy(),
                    ).append_policy(
                        context,
                        pool.id,
                        payload,
                        idempotency_key=idempotency_key,
                        ip="192.0.2.44",
                        user_agent="candidate-pool-postgres-race-test",
                    )

            tasks = [asyncio.create_task(append_policy()) for _ in range(2)]
            await start_barrier.wait()
            first, replay = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)

            assert lookup_race.empty_lookup_count == 2
            assert first.model_dump(mode="json") == replay.model_dump(mode="json")

            async with harness.factory() as session:
                record = await session.scalar(
                    select(Phase3AIdempotencyRecord).where(
                        Phase3AIdempotencyRecord.department_id == context.department.id,
                        Phase3AIdempotencyRecord.operation_scope
                        == Phase3AOperationScope.TARGETING_POLICY_CREATE,
                        Phase3AIdempotencyRecord.idempotency_key == idempotency_key,
                    )
                )
                assert record is not None
                assert record.result_entity_id == first.id
                persisted_pool = await session.get(CandidatePool, pool.id)
                assert persisted_pool is not None
                assert persisted_pool.current_policy_id == first.id
                assert (
                    int(
                        await session.scalar(
                            select(func.count())
                            .select_from(TargetingPolicy)
                            .where(TargetingPolicy.pool_id == pool.id)
                        )
                        or 0
                    )
                    == 2
                )
                assert (
                    int(
                        await session.scalar(
                            select(func.count())
                            .select_from(Phase3AIdempotencyRecord)
                            .where(
                                Phase3AIdempotencyRecord.department_id == context.department.id,
                                Phase3AIdempotencyRecord.operation_scope
                                == Phase3AOperationScope.TARGETING_POLICY_CREATE,
                                Phase3AIdempotencyRecord.idempotency_key == idempotency_key,
                            )
                        )
                        or 0
                    )
                    == 1
                )
                assert (
                    await _audit_count(
                        session,
                        action=AuditAction.TARGETING_POLICY_CREATED,
                        entity_id=first.id,
                    )
                ) == 1
                assert (
                    await _audit_count(
                        session,
                        action=AuditAction.CANDIDATE_POOL_UPDATED,
                        entity_id=pool.id,
                    )
                ) == 1

    asyncio.run(scenario())


def test_concurrent_policy_appends_with_distinct_keys_preserve_serial_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness:
            async with harness.factory() as setup_session:
                context = await _actor(setup_session)
                pool = await CandidatePoolService(
                    setup_session,
                    freshness_policy=FreshnessPolicy(),
                ).create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="PostgreSQL distinct policy-key race pool",
                        kind="POTENTIAL_SELLER",
                        policy=SellerTargetingPolicy(tags_exact_any=("beauty",)),
                    ),
                    idempotency_key="postgres-policy-distinct-bootstrap",
                    ip="192.0.2.44",
                    user_agent="candidate-pool-postgres-race-test",
                )
                assert pool.version == 2

            requests = (
                (
                    "postgres-policy-distinct-a",
                    TargetingPolicyCreateInput(
                        policy=SellerTargetingPolicy(followers=IntegerRange(minimum=100))
                    ),
                ),
                (
                    "postgres-policy-distinct-b",
                    TargetingPolicyCreateInput(
                        policy=SellerTargetingPolicy(followers=IntegerRange(minimum=200))
                    ),
                ),
            )
            idempotency_keys = frozenset(key for key, _payload in requests)
            lookup_race = _force_shared_record_lookup_race(
                monkeypatch,
                operation_scope=Phase3AOperationScope.TARGETING_POLICY_CREATE,
                idempotency_keys=idempotency_keys,
            )
            start_barrier = asyncio.Barrier(3)

            async def append_policy(
                idempotency_key: str,
                payload: TargetingPolicyCreateInput,
            ) -> TargetingPolicyCreateResultPublic:
                await start_barrier.wait()
                async with harness.factory() as session:
                    return await CandidatePoolService(
                        session,
                        freshness_policy=FreshnessPolicy(),
                    ).append_policy(
                        context,
                        pool.id,
                        payload,
                        idempotency_key=idempotency_key,
                        ip="192.0.2.44",
                        user_agent="candidate-pool-postgres-race-test",
                    )

            tasks = [
                asyncio.create_task(append_policy(idempotency_key, payload))
                for idempotency_key, payload in requests
            ]
            await start_barrier.wait()
            first, second = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)

            assert lookup_race.empty_lookup_count == 2
            assert first.id != second.id
            assert {first.version, second.version} == {2, 3}

            async with harness.factory() as session:
                persisted_pool = await session.get(CandidatePool, pool.id)
                assert persisted_pool is not None
                assert persisted_pool.version == 4
                latest = first if first.version == 3 else second
                assert persisted_pool.current_policy_id == latest.id

                policies = list(
                    await session.scalars(
                        select(TargetingPolicy)
                        .where(TargetingPolicy.pool_id == pool.id)
                        .order_by(TargetingPolicy.version)
                    )
                )
                assert [policy.version for policy in policies] == [1, 2, 3]
                assert {policy.id for policy in policies[1:]} == {first.id, second.id}

                records = list(
                    await session.scalars(
                        select(Phase3AIdempotencyRecord).where(
                            Phase3AIdempotencyRecord.department_id == context.department.id,
                            Phase3AIdempotencyRecord.operation_scope
                            == Phase3AOperationScope.TARGETING_POLICY_CREATE,
                            Phase3AIdempotencyRecord.idempotency_key.in_(idempotency_keys),
                        )
                    )
                )
                assert len(records) == 2
                assert {record.idempotency_key for record in records} == idempotency_keys
                assert {record.result_entity_id for record in records} == {first.id, second.id}

                assert (
                    await _audit_count(
                        session,
                        action=AuditAction.CANDIDATE_POOL_UPDATED,
                        entity_id=pool.id,
                    )
                ) == 2
                assert (
                    await _audit_count(
                        session,
                        action=AuditAction.TARGETING_POLICY_CREATED,
                        entity_id=first.id,
                    )
                ) == 1
                assert (
                    await _audit_count(
                        session,
                        action=AuditAction.TARGETING_POLICY_CREATED,
                        entity_id=second.id,
                    )
                ) == 1

    asyncio.run(scenario())


def test_postgres_materialization_uses_bounded_batch_queries() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as harness, harness.factory() as session:
            context = await _actor(session)
            assert context.operator is not None
            await _seed_accounts(session, owner_id=context.operator.id)
            await session.commit()

            service = CandidatePoolService(session, freshness_policy=FreshnessPolicy())
            pool = await service.create_pool(
                context,
                CandidatePoolCreateInput(
                    name="PostgreSQL bounded targeting",
                    kind="POTENTIAL_SELLER",
                    policy=SellerTargetingPolicy(tags_exact_any=("beauty",)),
                ),
                idempotency_key="postgres-bounded-targeting-pool",
            )
            run = await service.reserve_run(
                context,
                pool_id=pool.id,
                idempotency_key="postgres-bounded-targeting",
            )

            harness.probe.start()
            completed = await service.materialize_run(run.id)
            select_count = harness.probe.stop()

            assert completed is not None
            assert completed.match_count == ACCOUNT_COUNT
            assert completed.unknown_count == 0
            assert completed.not_match_count == 0
            # 501 accounts cross the 500-row batch boundary. Reads remain a
            # fixed per-batch set instead of scaling one query per account.
            assert select_count <= MAX_MATERIALIZATION_SELECTS

    asyncio.run(scenario())
