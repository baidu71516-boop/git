"""PostgreSQL 16 concurrency gates for Phase 3A Campaign and Outreach.

The module is opt-in: ``TEST_DATABASE_URL`` must identify the disposable
``phase1b_test`` PostgreSQL database. Every test upgrades a fresh random schema
to Alembic head, then drops that schema during teardown.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, Operator
from backend_core.auth.service import AuthContext
from backend_core.campaigns.channels import StaticChannelEnablementRegistry
from backend_core.campaigns.errors import CampaignOutreachError
from backend_core.campaigns.schemas import (
    CampaignCreateInput,
    CampaignMemberAddItem,
    CampaignMemberBulkAddInput,
    CampaignMemberFromCandidateRunBulkAddInput,
)
from backend_core.campaigns.service import CampaignService
from backend_core.config import get_settings
from backend_core.growth.enums import (
    CandidatePoolKind,
    CandidatePoolRunStatus,
    CandidatePoolStatus,
    CandidateResult,
    Phase3AOperationScope,
)
from backend_core.growth.idempotency import Phase3AIdempotencyRepository
from backend_core.growth.models import (
    Campaign,
    CampaignMember,
    CandidatePool,
    CandidatePoolMember,
    CandidatePoolRun,
    Phase3AIdempotencyRecord,
    TargetingPolicy,
)
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
    InfluencerPlatformAccount,
)
from backend_core.outreach.enums import OutreachChannel, OutreachEventType, OutreachTaskState
from backend_core.outreach.models import OutreachEvent, OutreachTarget, OutreachTask
from backend_core.outreach.schemas import (
    OutreachTargetCreateInput,
    OutreachTaskCreateInput,
    OutreachTaskTransitionInput,
)
from backend_core.outreach.service import OutreachService
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema, DropSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "infrastructure" / "migrations" / "alembic.ini"
MIGRATIONS = PROJECT_ROOT / "infrastructure" / "migrations"

CHANNELS = StaticChannelEnablementRegistry(frozenset({OutreachChannel.EMAIL}))
REQUEST_IP = "127.0.0.1"
REQUEST_AGENT = "phase3a-postgres-concurrency"


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
class PostgresHarness:
    factory: async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class ActorFixture:
    context: AuthContext
    department_id: UUID
    operator_id: UUID


@dataclass(frozen=True, slots=True)
class InfluencerFixture:
    influencer_id: UUID
    platform_account_id: UUID
    contact_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class CampaignMemberFixture:
    campaign_id: UUID
    member_id: UUID


@dataclass(frozen=True, slots=True)
class TaskFixture:
    task_id: UUID


@dataclass(frozen=True, slots=True)
class CandidateRunFixture:
    run_id: UUID
    match_member_ids: tuple[UUID, ...]


class _FirstLookupGate:
    """Make two service calls miss their initial durable replay lookup together."""

    def __init__(self, repository: Phase3AIdempotencyRepository, barrier: asyncio.Barrier) -> None:
        self._repository = repository
        self._barrier = barrier
        self._waited = False

    async def get(self, *args: Any, **kwargs: Any) -> Phase3AIdempotencyRecord | None:
        record = await self._repository.get(*args, **kwargs)
        if not self._waited:
            self._waited = True
            await self._barrier.wait()
        return record

    def add(self, **kwargs: Any) -> Phase3AIdempotencyRecord:
        return self._repository.add(**kwargs)


@pytest.fixture
def postgres_harness(monkeypatch: pytest.MonkeyPatch) -> Iterator[PostgresHarness]:
    """Create a migration-backed, PostgreSQL 16-only schema for one scenario."""

    schema_name = f"phase3a_campaign_outreach_{uuid4().hex}"
    admin_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    test_engine: AsyncEngine | None = None
    schema_created = False
    try:
        with admin_engine.begin() as connection:
            version_number = int(connection.scalar(text("SHOW server_version_num")))
            assert version_number // 10_000 == 16
            connection.execute(CreateSchema(schema_name))
        schema_created = True

        scoped_url = TEST_DATABASE_URL.update_query_dict(
            {"options": f"-csearch_path={schema_name}"}
        )
        monkeypatch.setenv(
            "DATABASE_URL",
            scoped_url.render_as_string(hide_password=False).replace("%", "%%"),
        )
        get_settings.cache_clear()
        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(MIGRATIONS))
        command.upgrade(config, "head")

        test_engine = create_async_engine(scoped_url, pool_pre_ping=True)
        yield PostgresHarness(async_sessionmaker(test_engine, expire_on_commit=False))
    finally:
        if test_engine is not None:
            asyncio.run(test_engine.dispose())
        get_settings.cache_clear()
        try:
            if schema_created:
                with admin_engine.begin() as connection:
                    connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
        finally:
            admin_engine.dispose()


async def _seed_actor(factory: async_sessionmaker[AsyncSession]) -> ActorFixture:
    async with factory() as session:
        department = Department(
            name=f"Phase 3A PostgreSQL {uuid4().hex}",
            password_hash="not-used-by-postgres-concurrency-test",
            status=DepartmentStatus.ACTIVE,
            session_days=30,
        )
        session.add(department)
        await session.flush()
        operator = Operator(
            department_id=department.id,
            name="Phase 3A PostgreSQL operator",
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
            ip=REQUEST_IP,
            user_agent=REQUEST_AGENT,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            revoked_at=None,
        )
        session.add(auth_session)
        await session.commit()
        return ActorFixture(
            context=AuthContext(
                department=department,
                operator=operator,
                role=Role.OPERATOR,
                auth_session=auth_session,
            ),
            department_id=department.id,
            operator_id=operator.id,
        )


async def _seed_influencer(
    factory: async_sessionmaker[AsyncSession],
    actor: ActorFixture,
    *,
    contact_count: int = 1,
) -> InfluencerFixture:
    assert contact_count >= 1
    async with factory() as session:
        influencer = Influencer(
            display_name=f"Phase 3A influencer {uuid4().hex}",
            owner_operator_id=actor.operator_id,
            crm_stage=CRMStage.TO_DEVELOP,
            status=InfluencerStatus.ACTIVE,
            deleted_at=None,
        )
        session.add(influencer)
        await session.flush()
        account = InfluencerPlatformAccount(
            influencer_id=influencer.id,
            platform=Platform.XIAOHONGSHU,
            platform_account_id=f"phase3a-{uuid4().hex}",
            account_name=f"Phase 3A account {uuid4().hex}",
            source=DataSource.MANUAL,
            is_active=True,
        )
        session.add(account)
        await session.flush()
        contact_ids: list[UUID] = []
        now = datetime.now(UTC)
        for index in range(contact_count):
            value = f"phase3a-{uuid4().hex}-{index}@example.invalid"
            contact = InfluencerContact(
                influencer_id=influencer.id,
                platform_account_id=None,
                type=ContactType.EMAIL,
                value=value,
                normalized_value=value.lower(),
                source=DataSource.MANUAL,
                validation_status=ContactValidationStatus.VALID,
                is_current=True,
                possible_duplicate_contact=False,
                first_seen_at=now,
                last_seen_at=now,
                source_updated_at=None,
                first_import_job_id=None,
                first_import_row_id=None,
                last_import_job_id=None,
                last_import_row_id=None,
            )
            session.add(contact)
            await session.flush()
            contact_ids.append(contact.id)
        await session.commit()
        return InfluencerFixture(
            influencer_id=influencer.id,
            platform_account_id=account.id,
            contact_ids=tuple(contact_ids),
        )


async def _seed_completed_match_run(
    factory: async_sessionmaker[AsyncSession],
    actor: ActorFixture,
    *,
    match_count: int,
) -> CandidateRunFixture:
    """Create one completed immutable source run with enough distinct MATCH rows to page."""

    assert actor.context.operator is not None
    assert match_count > CampaignService._ALL_MATCH_SOURCE_CHUNK_SIZE
    async with factory() as session:
        pool = CandidatePool(
            department_id=actor.department_id,
            owner_operator_id=actor.operator_id,
            name=f"ALL_MATCH PostgreSQL pool {uuid4().hex}",
            kind=CandidatePoolKind.POTENTIAL_SELLER,
            source_collection_job_id=None,
            status=CandidatePoolStatus.ACTIVE,
            current_policy_id=None,
            version=1,
        )
        session.add(pool)
        await session.flush()
        policy = TargetingPolicy(
            pool_id=pool.id,
            version=1,
            schema_version=1,
            definition={},
            canonical_hash="0" * 64,
            created_by_operator_id=actor.operator_id,
        )
        session.add(policy)
        await session.flush()
        run = CandidatePoolRun(
            pool_id=pool.id,
            policy_id=policy.id,
            as_of=datetime.now(UTC),
            input_watermark=None,
            status=CandidatePoolRunStatus.COMPLETED,
            match_count=match_count,
            unknown_count=0,
            not_match_count=0,
            error_code=None,
            error_message=None,
            idempotency_key=f"all-match-run-{uuid4().hex}",
            request_hash="0" * 64,
        )
        session.add(run)
        await session.flush()

        influencers: list[Influencer] = []
        accounts: list[InfluencerPlatformAccount] = []
        members: list[CandidatePoolMember] = []
        member_ids: list[UUID] = []
        for index in range(match_count):
            influencer_id = uuid4()
            account_id = uuid4()
            member_id = uuid4()
            influencers.append(
                Influencer(
                    id=influencer_id,
                    display_name=f"ALL_MATCH influencer {index}",
                    owner_operator_id=actor.operator_id,
                    crm_stage=CRMStage.TO_DEVELOP,
                    status=InfluencerStatus.ACTIVE,
                    deleted_at=None,
                )
            )
            accounts.append(
                InfluencerPlatformAccount(
                    id=account_id,
                    influencer_id=influencer_id,
                    platform=Platform.XIAOHONGSHU,
                    platform_account_id=f"all-match-{uuid4().hex}",
                    account_name=f"ALL_MATCH account {index}",
                    source=DataSource.MANUAL,
                    is_active=True,
                )
            )
            members.append(
                CandidatePoolMember(
                    id=member_id,
                    run_id=run.id,
                    influencer_id=influencer_id,
                    platform_account_id=account_id,
                    result=CandidateResult.MATCH,
                    reason_codes=[],
                    redacted_evidence={},
                    evidence_hash="0" * 64,
                )
            )
            member_ids.append(member_id)
        session.add_all(influencers)
        await session.flush()
        session.add_all(accounts)
        await session.flush()
        session.add_all(members)
        await session.commit()
        return CandidateRunFixture(run_id=run.id, match_member_ids=tuple(member_ids))


async def _seed_campaign_member(
    factory: async_sessionmaker[AsyncSession],
    actor: ActorFixture,
    influencer: InfluencerFixture,
) -> CampaignMemberFixture:
    async with factory() as session:
        campaigns = CampaignService(session, channel_registry=CHANNELS)
        created = await campaigns.create_campaign(
            actor.context,
            CampaignCreateInput(name=f"Phase 3A campaign {uuid4().hex}"),
            idempotency_key=f"setup-campaign-{uuid4().hex}",
            ip=REQUEST_IP,
            user_agent=REQUEST_AGENT,
        )
        campaign_id = created.result.id
        await campaigns.bulk_add_members(
            actor.context,
            campaign_id,
            CampaignMemberBulkAddInput(
                members=(
                    CampaignMemberAddItem(
                        influencer_id=influencer.influencer_id,
                        preferred_platform_account_id=influencer.platform_account_id,
                    ),
                )
            ),
            idempotency_key=f"setup-member-{uuid4().hex}",
            ip=REQUEST_IP,
            user_agent=REQUEST_AGENT,
        )
        member_id = await session.scalar(
            select(CampaignMember.id).where(
                CampaignMember.campaign_id == campaign_id,
                CampaignMember.influencer_id == influencer.influencer_id,
            )
        )
        assert isinstance(member_id, UUID)
        return CampaignMemberFixture(campaign_id=campaign_id, member_id=member_id)


async def _seed_task(
    factory: async_sessionmaker[AsyncSession],
    actor: ActorFixture,
    influencer: InfluencerFixture,
) -> TaskFixture:
    campaign_member = await _seed_campaign_member(factory, actor, influencer)
    async with factory() as session:
        outreach = OutreachService(session, channel_registry=CHANNELS)
        target = await outreach.create_target(
            actor.context,
            campaign_member.campaign_id,
            OutreachTargetCreateInput(
                member_id=campaign_member.member_id,
                influencer_id=influencer.influencer_id,
                channel=OutreachChannel.EMAIL,
                contact_id=influencer.contact_ids[0],
            ),
            idempotency_key=f"setup-target-{uuid4().hex}",
            ip=REQUEST_IP,
            user_agent=REQUEST_AGENT,
        )
        task = await outreach.create_task(
            actor.context,
            target.result.id,
            OutreachTaskCreateInput(due_at=datetime(2030, 1, 1, tzinfo=UTC)),
            idempotency_key=f"setup-task-{uuid4().hex}",
            ip=REQUEST_IP,
            user_agent=REQUEST_AGENT,
        )
        assert task.result.task.state is OutreachTaskState.REVIEW_REQUIRED
        return TaskFixture(task_id=task.result.task.id)


async def _assert_independent_sessions(first: AsyncSession, second: AsyncSession) -> None:
    first_pid, second_pid = (
        int(value)
        for value in await asyncio.gather(
            first.scalar(text("SELECT pg_backend_pid()")),
            second.scalar(text("SELECT pg_backend_pid()")),
        )
    )
    assert first is not second
    assert first_pid != second_pid


def _gate_first_idempotency_lookup(
    service: CampaignService | OutreachService,
    barrier: asyncio.Barrier,
) -> None:
    service.idempotency = _FirstLookupGate(service.idempotency, barrier)  # type: ignore[assignment]


def _gate_before_first_task_lock(service: OutreachService, barrier: asyncio.Barrier) -> None:
    original = service._locked_task
    waiting = False

    async def gated(task_id: UUID, scope: Any) -> OutreachTask:
        nonlocal waiting
        if not waiting:
            waiting = True
            await barrier.wait()
        return await original(task_id, scope)

    service._locked_task = gated  # type: ignore[method-assign]


async def _count(session: AsyncSession, model: Any, *predicates: Any) -> int:
    statement = select(func.count()).select_from(model)
    if predicates:
        statement = statement.where(*predicates)
    return int(await session.scalar(statement) or 0)


def test_campaign_create_same_key_race_replays_and_rejects_different_payload(
    postgres_harness: PostgresHarness,
) -> None:
    asyncio.run(_campaign_create_same_key_race(postgres_harness))


async def _campaign_create_same_key_race(harness: PostgresHarness) -> None:
    actor = await _seed_actor(harness.factory)
    key = f"campaign-race-{uuid4().hex}"
    request = CampaignCreateInput(name="Campaign idempotency race")
    barrier = asyncio.Barrier(2)

    async with harness.factory() as first_session, harness.factory() as second_session:
        await _assert_independent_sessions(first_session, second_session)
        first_service = CampaignService(first_session, channel_registry=CHANNELS)
        second_service = CampaignService(second_session, channel_registry=CHANNELS)
        _gate_first_idempotency_lookup(first_service, barrier)
        _gate_first_idempotency_lookup(second_service, barrier)
        first, second = await asyncio.gather(
            first_service.create_campaign(
                actor.context,
                request,
                idempotency_key=key,
                ip=REQUEST_IP,
                user_agent=REQUEST_AGENT,
            ),
            second_service.create_campaign(
                actor.context,
                request,
                idempotency_key=key,
                ip=REQUEST_IP,
                user_agent=REQUEST_AGENT,
            ),
        )

    assert first.status_code == second.status_code == 201
    assert first.result == second.result
    assert {first.replayed, second.replayed} == {False, True}

    async with harness.factory() as session:
        with pytest.raises(CampaignOutreachError) as reused:
            await CampaignService(session, channel_registry=CHANNELS).create_campaign(
                actor.context,
                CampaignCreateInput(name="Different campaign request"),
                idempotency_key=key,
                ip=REQUEST_IP,
                user_agent=REQUEST_AGENT,
            )
        assert reused.value.code == "IDEMPOTENCY_KEY_REUSED"

    async with harness.factory() as session:
        assert (
            await _count(
                session,
                Campaign,
                Campaign.department_id == actor.department_id,
                Campaign.name == request.name,
            )
            == 1
        )
        assert (
            await _count(
                session,
                Phase3AIdempotencyRecord,
                Phase3AIdempotencyRecord.department_id == actor.department_id,
                Phase3AIdempotencyRecord.operation_scope == Phase3AOperationScope.CAMPAIGN_CREATE,
                Phase3AIdempotencyRecord.idempotency_key == key,
            )
            == 1
        )
        assert (
            await _count(
                session,
                AuditLog,
                AuditLog.action == AuditAction.CAMPAIGN_CREATED,
            )
            == 1
        )


def test_campaign_member_bulk_add_same_key_race_has_one_member_audit_and_record(
    postgres_harness: PostgresHarness,
) -> None:
    asyncio.run(_campaign_member_bulk_add_same_key_race(postgres_harness))


async def _campaign_member_bulk_add_same_key_race(harness: PostgresHarness) -> None:
    actor = await _seed_actor(harness.factory)
    influencer = await _seed_influencer(harness.factory, actor)
    async with harness.factory() as session:
        campaign = await CampaignService(session, channel_registry=CHANNELS).create_campaign(
            actor.context,
            CampaignCreateInput(name="Campaign member race"),
            idempotency_key=f"setup-campaign-{uuid4().hex}",
            ip=REQUEST_IP,
            user_agent=REQUEST_AGENT,
        )
    campaign_id = campaign.result.id
    key = f"member-race-{uuid4().hex}"
    request = CampaignMemberBulkAddInput(
        members=(
            CampaignMemberAddItem(
                influencer_id=influencer.influencer_id,
                preferred_platform_account_id=influencer.platform_account_id,
            ),
        )
    )
    barrier = asyncio.Barrier(2)

    async with harness.factory() as first_session, harness.factory() as second_session:
        await _assert_independent_sessions(first_session, second_session)
        first_service = CampaignService(first_session, channel_registry=CHANNELS)
        second_service = CampaignService(second_session, channel_registry=CHANNELS)
        _gate_first_idempotency_lookup(first_service, barrier)
        _gate_first_idempotency_lookup(second_service, barrier)
        first, second = await asyncio.gather(
            first_service.bulk_add_members(
                actor.context,
                campaign_id,
                request,
                idempotency_key=key,
                ip=REQUEST_IP,
                user_agent=REQUEST_AGENT,
            ),
            second_service.bulk_add_members(
                actor.context,
                campaign_id,
                request,
                idempotency_key=key,
                ip=REQUEST_IP,
                user_agent=REQUEST_AGENT,
            ),
        )

    assert first.status_code == second.status_code == 200
    assert first.result == second.result
    assert first.result.added_count == 1
    assert {first.replayed, second.replayed} == {False, True}

    async with harness.factory() as session:
        assert (
            await _count(
                session,
                CampaignMember,
                CampaignMember.campaign_id == campaign_id,
                CampaignMember.influencer_id == influencer.influencer_id,
            )
            == 1
        )
        assert (
            await _count(
                session,
                Phase3AIdempotencyRecord,
                Phase3AIdempotencyRecord.department_id == actor.department_id,
                Phase3AIdempotencyRecord.operation_scope
                == Phase3AOperationScope.CAMPAIGN_MEMBER_BULK_ADD,
                Phase3AIdempotencyRecord.idempotency_key == key,
            )
            == 1
        )
        assert (
            await _count(
                session,
                AuditLog,
                AuditLog.action == AuditAction.CAMPAIGN_MEMBERS_ADDED,
            )
            == 1
        )


def test_all_match_large_postgres_run_is_chunked_replayable_and_atomic(
    postgres_harness: PostgresHarness,
) -> None:
    asyncio.run(_all_match_large_postgres_run_is_chunked_replayable_and_atomic(postgres_harness))


async def _all_match_large_postgres_run_is_chunked_replayable_and_atomic(
    harness: PostgresHarness,
) -> None:
    source_chunk_size = CampaignService._ALL_MATCH_SOURCE_CHUNK_SIZE
    actor = await _seed_actor(harness.factory)
    source = await _seed_completed_match_run(
        harness.factory,
        actor,
        match_count=source_chunk_size + 5,
    )
    excluded_member_ids = source.match_member_ids[:5]
    expected_count = len(source.match_member_ids) - len(excluded_member_ids)
    request = CampaignMemberFromCandidateRunBulkAddInput(
        run_id=source.run_id,
        selection_mode="ALL_MATCH",
        excluded_member_ids=excluded_member_ids,
    )
    success_key = f"all-match-large-success-{uuid4().hex}"

    async with harness.factory() as session:
        service = CampaignService(session, channel_registry=CHANNELS)
        campaign = await service.create_campaign(
            actor.context,
            CampaignCreateInput(name="ALL_MATCH PostgreSQL large run"),
            idempotency_key=f"setup-all-match-campaign-{uuid4().hex}",
            ip=REQUEST_IP,
            user_agent=REQUEST_AGENT,
        )
        campaign_id = campaign.result.id
        page_limits: list[int] = []
        original_page_reader = service.repository.list_matching_source_run_members_page

        async def record_source_page(
            *,
            source_pool_run_id: UUID,
            after_member_id: UUID | None,
            limit: int,
        ) -> Any:
            assert source_pool_run_id == source.run_id
            page_limits.append(limit)
            return await original_page_reader(
                source_pool_run_id=source_pool_run_id,
                after_member_id=after_member_id,
                limit=limit,
            )

        service.repository.list_matching_source_run_members_page = record_source_page  # type: ignore[method-assign]
        first = await service.bulk_add_members_from_candidate_run(
            actor.context,
            campaign_id,
            request,
            idempotency_key=success_key,
            ip=REQUEST_IP,
            user_agent=REQUEST_AGENT,
        )
        pages_before_replay = len(page_limits)
        replay = await service.bulk_add_members_from_candidate_run(
            actor.context,
            campaign_id,
            request,
            idempotency_key=success_key,
            ip=REQUEST_IP,
            user_agent=REQUEST_AGENT,
        )

        assert first.result.requested_count == expected_count
        assert first.result.added_count == expected_count
        assert first.result.restored_count == 0
        assert first.result.already_active_count == 0
        assert first.result.active_count_after == expected_count
        assert first.result.source_pool_run_id == source.run_id
        assert replay.replayed
        assert replay.result == first.result
        assert len(page_limits) == pages_before_replay
        assert len(page_limits) >= 2
        assert max(page_limits) <= source_chunk_size
        assert (
            await _count(
                session,
                CampaignMember,
                CampaignMember.campaign_id == campaign_id,
            )
            == expected_count
        )
        assert set(
            await session.scalars(
                select(CampaignMember.source_pool_run_id).where(
                    CampaignMember.campaign_id == campaign_id
                )
            )
        ) == {source.run_id}
        assert (
            await _count(
                session,
                AuditLog,
                AuditLog.action == AuditAction.CAMPAIGN_MEMBERS_ADDED,
                AuditLog.entity_id == campaign_id,
            )
            == 1
        )
        assert (
            await _count(
                session,
                Phase3AIdempotencyRecord,
                Phase3AIdempotencyRecord.department_id == actor.department_id,
                Phase3AIdempotencyRecord.operation_scope
                == Phase3AOperationScope.CAMPAIGN_MEMBER_BULK_ADD,
                Phase3AIdempotencyRecord.idempotency_key == success_key,
            )
            == 1
        )

    rollback_key = f"all-match-large-rollback-{uuid4().hex}"
    async with harness.factory() as session:
        service = CampaignService(session, channel_registry=CHANNELS)
        rollback_campaign = await service.create_campaign(
            actor.context,
            CampaignCreateInput(name="ALL_MATCH PostgreSQL rollback"),
            idempotency_key=f"setup-all-match-rollback-campaign-{uuid4().hex}",
            ip=REQUEST_IP,
            user_agent=REQUEST_AGENT,
        )
        rollback_campaign_id = rollback_campaign.result.id
        insert_calls = 0
        original_insert = service.repository.insert_campaign_members

        async def fail_second_source_write(members: Any) -> None:
            nonlocal insert_calls
            insert_calls += 1
            if insert_calls == 2:
                raise RuntimeError("forced all-match second source write failure")
            await original_insert(members)

        service.repository.insert_campaign_members = fail_second_source_write  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="forced all-match second source write failure"):
            await service.bulk_add_members_from_candidate_run(
                actor.context,
                rollback_campaign_id,
                CampaignMemberFromCandidateRunBulkAddInput(
                    run_id=source.run_id,
                    selection_mode="ALL_MATCH",
                    excluded_member_ids=(),
                ),
                idempotency_key=rollback_key,
                ip=REQUEST_IP,
                user_agent=REQUEST_AGENT,
            )
        assert insert_calls == 2

    async with harness.factory() as session:
        assert (
            await _count(
                session,
                CampaignMember,
                CampaignMember.campaign_id == rollback_campaign_id,
            )
            == 0
        )
        assert (
            await _count(
                session,
                AuditLog,
                AuditLog.action == AuditAction.CAMPAIGN_MEMBERS_ADDED,
                AuditLog.entity_id == rollback_campaign_id,
            )
            == 0
        )
        assert (
            await _count(
                session,
                Phase3AIdempotencyRecord,
                Phase3AIdempotencyRecord.department_id == actor.department_id,
                Phase3AIdempotencyRecord.operation_scope
                == Phase3AOperationScope.CAMPAIGN_MEMBER_BULK_ADD,
                Phase3AIdempotencyRecord.idempotency_key == rollback_key,
            )
            == 0
        )


def test_outreach_target_same_key_replays_and_rejects_different_endpoint(
    postgres_harness: PostgresHarness,
) -> None:
    asyncio.run(_outreach_target_same_key_race(postgres_harness))


async def _outreach_target_same_key_race(harness: PostgresHarness) -> None:
    actor = await _seed_actor(harness.factory)
    influencer = await _seed_influencer(harness.factory, actor, contact_count=2)
    campaign_member = await _seed_campaign_member(harness.factory, actor, influencer)
    key = f"target-race-{uuid4().hex}"
    request = OutreachTargetCreateInput(
        member_id=campaign_member.member_id,
        influencer_id=influencer.influencer_id,
        channel=OutreachChannel.EMAIL,
        contact_id=influencer.contact_ids[0],
    )
    barrier = asyncio.Barrier(2)

    async with harness.factory() as first_session, harness.factory() as second_session:
        await _assert_independent_sessions(first_session, second_session)
        first_service = OutreachService(first_session, channel_registry=CHANNELS)
        second_service = OutreachService(second_session, channel_registry=CHANNELS)
        _gate_first_idempotency_lookup(first_service, barrier)
        _gate_first_idempotency_lookup(second_service, barrier)
        first, second = await asyncio.gather(
            first_service.create_target(
                actor.context,
                campaign_member.campaign_id,
                request,
                idempotency_key=key,
                ip=REQUEST_IP,
                user_agent=REQUEST_AGENT,
            ),
            second_service.create_target(
                actor.context,
                campaign_member.campaign_id,
                request,
                idempotency_key=key,
                ip=REQUEST_IP,
                user_agent=REQUEST_AGENT,
            ),
        )

    assert first.status_code == second.status_code == 201
    assert first.result == second.result
    assert first.result.masked_display == "***"
    assert {first.replayed, second.replayed} == {False, True}

    async with harness.factory() as session:
        with pytest.raises(CampaignOutreachError) as reused:
            await OutreachService(session, channel_registry=CHANNELS).create_target(
                actor.context,
                campaign_member.campaign_id,
                OutreachTargetCreateInput(
                    member_id=campaign_member.member_id,
                    influencer_id=influencer.influencer_id,
                    channel=OutreachChannel.EMAIL,
                    contact_id=influencer.contact_ids[1],
                ),
                idempotency_key=key,
                ip=REQUEST_IP,
                user_agent=REQUEST_AGENT,
            )
        assert reused.value.code == "IDEMPOTENCY_KEY_REUSED"

    async with harness.factory() as session:
        assert (
            await _count(
                session,
                OutreachTarget,
                OutreachTarget.campaign_id == campaign_member.campaign_id,
                OutreachTarget.member_id == campaign_member.member_id,
                OutreachTarget.channel == OutreachChannel.EMAIL,
            )
            == 1
        )
        assert (
            await _count(
                session,
                Phase3AIdempotencyRecord,
                Phase3AIdempotencyRecord.department_id == actor.department_id,
                Phase3AIdempotencyRecord.operation_scope
                == Phase3AOperationScope.OUTREACH_TARGET_CREATE,
                Phase3AIdempotencyRecord.idempotency_key == key,
            )
            == 1
        )
        assert (
            await _count(
                session,
                AuditLog,
                AuditLog.action == AuditAction.OUTREACH_TARGET_CREATED,
            )
            == 1
        )


def test_outreach_target_same_key_different_endpoint_race_reuses_key(
    postgres_harness: PostgresHarness,
) -> None:
    asyncio.run(_outreach_target_same_key_different_endpoint_race(postgres_harness))


async def _outreach_target_same_key_different_endpoint_race(harness: PostgresHarness) -> None:
    actor = await _seed_actor(harness.factory)
    influencer = await _seed_influencer(harness.factory, actor, contact_count=2)
    campaign_member = await _seed_campaign_member(harness.factory, actor, influencer)
    key = f"target-endpoint-race-{uuid4().hex}"
    first_request = OutreachTargetCreateInput(
        member_id=campaign_member.member_id,
        influencer_id=influencer.influencer_id,
        channel=OutreachChannel.EMAIL,
        contact_id=influencer.contact_ids[0],
    )
    second_request = OutreachTargetCreateInput(
        member_id=campaign_member.member_id,
        influencer_id=influencer.influencer_id,
        channel=OutreachChannel.EMAIL,
        contact_id=influencer.contact_ids[1],
    )
    barrier = asyncio.Barrier(2)

    async with harness.factory() as first_session, harness.factory() as second_session:
        await _assert_independent_sessions(first_session, second_session)
        first_service = OutreachService(first_session, channel_registry=CHANNELS)
        second_service = OutreachService(second_session, channel_registry=CHANNELS)
        _gate_first_idempotency_lookup(first_service, barrier)
        _gate_first_idempotency_lookup(second_service, barrier)
        outcomes = await asyncio.gather(
            first_service.create_target(
                actor.context,
                campaign_member.campaign_id,
                first_request,
                idempotency_key=key,
                ip=REQUEST_IP,
                user_agent=REQUEST_AGENT,
            ),
            second_service.create_target(
                actor.context,
                campaign_member.campaign_id,
                second_request,
                idempotency_key=key,
                ip=REQUEST_IP,
                user_agent=REQUEST_AGENT,
            ),
            return_exceptions=True,
        )

    successes = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert not any(
        isinstance(outcome, BaseException) and not isinstance(outcome, CampaignOutreachError)
        for outcome in outcomes
    )
    assert successes[0].status_code == 201
    assert successes[0].result.contact_id in set(influencer.contact_ids)
    assert isinstance(failures[0], CampaignOutreachError)
    assert failures[0].code == "IDEMPOTENCY_KEY_REUSED"

    async with harness.factory() as session:
        target = await session.scalar(
            select(OutreachTarget).where(
                OutreachTarget.campaign_id == campaign_member.campaign_id,
                OutreachTarget.member_id == campaign_member.member_id,
                OutreachTarget.channel == OutreachChannel.EMAIL,
            )
        )
        assert target is not None
        assert target.contact_id == successes[0].result.contact_id
        assert (
            await _count(
                session,
                OutreachTarget,
                OutreachTarget.campaign_id == campaign_member.campaign_id,
                OutreachTarget.member_id == campaign_member.member_id,
                OutreachTarget.channel == OutreachChannel.EMAIL,
            )
            == 1
        )
        assert (
            await _count(
                session,
                Phase3AIdempotencyRecord,
                Phase3AIdempotencyRecord.department_id == actor.department_id,
                Phase3AIdempotencyRecord.operation_scope
                == Phase3AOperationScope.OUTREACH_TARGET_CREATE,
                Phase3AIdempotencyRecord.idempotency_key == key,
            )
            == 1
        )
        assert (
            await _count(
                session,
                AuditLog,
                AuditLog.action == AuditAction.OUTREACH_TARGET_CREATED,
            )
            == 1
        )


def test_stale_task_transition_cas_creates_one_transition_event_and_audit(
    postgres_harness: PostgresHarness,
) -> None:
    asyncio.run(_stale_task_transition_cas(postgres_harness))


async def _stale_task_transition_cas(harness: PostgresHarness) -> None:
    actor = await _seed_actor(harness.factory)
    influencer = await _seed_influencer(harness.factory, actor)
    task = await _seed_task(harness.factory, actor, influencer)
    first_key = f"task-transition-first-{uuid4().hex}"
    second_key = f"task-transition-second-{uuid4().hex}"
    request = OutreachTaskTransitionInput(
        to_state=OutreachTaskState.READY,
        expected_version=1,
    )
    barrier = asyncio.Barrier(2)

    async with harness.factory() as first_session, harness.factory() as second_session:
        await _assert_independent_sessions(first_session, second_session)
        first_service = OutreachService(first_session, channel_registry=CHANNELS)
        second_service = OutreachService(second_session, channel_registry=CHANNELS)
        _gate_before_first_task_lock(first_service, barrier)
        _gate_before_first_task_lock(second_service, barrier)
        outcomes = await asyncio.gather(
            first_service.transition_task(
                actor.context,
                task.task_id,
                request,
                idempotency_key=first_key,
                ip=REQUEST_IP,
                user_agent=REQUEST_AGENT,
            ),
            second_service.transition_task(
                actor.context,
                task.task_id,
                request,
                idempotency_key=second_key,
                ip=REQUEST_IP,
                user_agent=REQUEST_AGENT,
            ),
            return_exceptions=True,
        )

    successes = [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert not any(
        isinstance(outcome, BaseException) and not isinstance(outcome, CampaignOutreachError)
        for outcome in outcomes
    )
    assert isinstance(failures[0], CampaignOutreachError)
    assert failures[0].code == "VERSION_CONFLICT"
    assert successes[0].state is OutreachTaskState.READY
    assert successes[0].version == 2

    async with harness.factory() as session:
        stored_task = await session.get(OutreachTask, task.task_id)
        assert stored_task is not None
        assert stored_task.state is OutreachTaskState.READY
        assert stored_task.version == 2
        assert (
            await _count(
                session,
                OutreachEvent,
                OutreachEvent.task_id == task.task_id,
                OutreachEvent.event_type == OutreachEventType.REVIEW_APPROVED,
            )
            == 1
        )
        assert (
            await _count(
                session,
                OutreachEvent,
                OutreachEvent.task_id == task.task_id,
                OutreachEvent.idempotency_key.in_((first_key, second_key)),
            )
            == 1
        )
        assert (
            await _count(
                session,
                AuditLog,
                AuditLog.action == AuditAction.OUTREACH_TASK_TRANSITIONED,
                AuditLog.entity_id == task.task_id,
            )
            == 1
        )
