"""Portable orchestration coverage for the Task 8 refresh queue service."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.auth.service import AuthContext
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.influencers.enums import (
    CRMStage,
    DataSource,
    InfluencerStatus,
    Platform,
)
from backend_core.influencers.freshness import FreshnessPolicy, FreshnessStatus
from backend_core.influencers.models import Influencer, InfluencerPlatformAccount
from backend_core.refresh.enums import (
    RefreshPriorityReason,
    RefreshQueueItemStatus,
    RefreshQueueStatus,
)
from backend_core.refresh.models import RefreshQueue, RefreshQueueItem
from backend_core.refresh.repository import RefreshCandidateRecord, RefreshQueueRepository
from backend_core.refresh.schemas import (
    RefreshQueueCreateInput,
    RefreshQueueItemListQuery,
    RefreshQueueListQuery,
)
from backend_core.refresh.service import RefreshQueueError, RefreshQueueService
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

NOW = datetime(2026, 8, 13, 8, 0, tzinfo=UTC)


@dataclass
class MutableClock:
    value: datetime = NOW

    def now(self) -> datetime:
        return self.value

    def advance(self) -> None:
        self.value += timedelta(minutes=1)


class FixedCandidateRepository(RefreshQueueRepository):
    def __init__(
        self,
        session: AsyncSession,
        candidate: RefreshCandidateRecord,
    ) -> None:
        super().__init__(session)
        self.candidate = candidate
        self.events: list[str] = []
        self.as_of_values: list[datetime] = []

    async def acquire_department_creation_lock(self, department_id: object) -> None:
        self.events.append(f"department:{department_id}")

    async def acquire_candidate_locks(
        self,
        department_id: object,
        candidates: object,
    ) -> None:
        self.events.append(f"candidates:{department_id}")

    async def list_candidates(
        self,
        *,
        department_id: object,
        as_of: datetime,
        policy: FreshnessPolicy,
        limit: int,
    ) -> list[RefreshCandidateRecord]:
        del policy
        self.events.append(f"select:{department_id}")
        self.as_of_values.append(as_of)
        return [self.candidate][:limit]


async def _seed_actor(
    session: AsyncSession,
    *,
    name: str,
    role: Role,
    status: DepartmentStatus = DepartmentStatus.ACTIVE,
) -> AuthContext:
    department = Department(
        name=f"{name}-{uuid4().hex}",
        password_hash="not-used-by-refresh-service-test",
        status=status,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name=f"{name} operator",
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    permission = DepartmentPermission(department_id=department.id, role=role)
    session.add_all([operator, permission])
    await session.flush()
    auth_session = AuthSession(
        department_id=department.id,
        operator_id=operator.id,
        token_hash=uuid4().hex * 2,
        csrf_token_hash=uuid4().hex * 2,
        ip="127.0.0.1",
        user_agent="refresh-service-test",
        expires_at=NOW + timedelta(days=1),
    )
    session.add(auth_session)
    await session.flush()
    return AuthContext(
        department=department,
        operator=operator,
        role=role,
        auth_session=auth_session,
    )


async def _seed_candidate(
    session: AsyncSession,
    owner: Operator,
) -> tuple[InfluencerPlatformAccount, RefreshCandidateRecord]:
    influencer = Influencer(
        display_name="Service Candidate",
        owner_operator_id=owner.id,
        crm_stage=CRMStage.TO_DEVELOP,
        status=InfluencerStatus.ACTIVE,
        deleted_at=None,
    )
    session.add(influencer)
    await session.flush()
    account = InfluencerPlatformAccount(
        influencer_id=influencer.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id="service-account-id",
        account_name="Live name",
        account_handle="service-handle",
        profile_url="https://example.invalid/service-account",
        normalized_profile_url="https://example.invalid/service-account",
        source=DataSource.HUITUN,
        is_active=True,
    )
    session.add(account)
    await session.flush()
    return account, RefreshCandidateRecord(
        influencer_id=influencer.id,
        platform_account_id=account.id,
        platform=Platform.XIAOHONGSHU.value,
        external_platform_account_id="service-account-id",
        account_name='  =HYPERLINK("https://example.invalid")',
        account_handle="service-handle",
        profile_url="https://example.invalid/service-account",
        external_source_id="huitun-source-id",
        followers_count=1234,
        last_observed_at=NOW - timedelta(days=120),
        baseline_source_updated_at=NOW - timedelta(days=121),
        freshness_status=FreshnessStatus.VERY_STALE,
        priority_tier=2,
        priority_reasons=(RefreshPriorityReason.VERY_STALE,),
    )


def _create_input(*, department_id: object | None = None) -> RefreshQueueCreateInput:
    return RefreshQueueCreateInput(
        department_id=department_id,
        requested_limit=1,
        today_total_limit=10,
        refresh_limit=5,
    )


def test_service_create_read_export_cancel_rbac_and_audit_contract() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                manager = await _seed_actor(session, name="manager", role=Role.MANAGER)
                other = await _seed_actor(session, name="other", role=Role.OPERATOR)
                other_department_id = other.department.id
                assert manager.operator is not None
                account, candidate = await _seed_candidate(session, manager.operator)
                await session.commit()

                clock = MutableClock()
                repository = FixedCandidateRepository(session, candidate)
                service = RefreshQueueService(
                    session,
                    freshness_policy=FreshnessPolicy(),
                    clock=clock,
                    repository=repository,
                )
                detail = await service.create_queue(
                    manager,
                    _create_input(),
                    "127.0.0.1",
                    "refresh-service-test",
                )
                assert detail.queue.status is RefreshQueueStatus.OPEN
                assert detail.queue.as_of == NOW
                assert detail.summary.selected == 1
                assert detail.summary.unique_influencers == 1
                assert detail.summary.priority_breakdown == {2: 1}
                assert detail.summary.freshness_breakdown == {FreshnessStatus.VERY_STALE: 1}
                assert repository.as_of_values == [NOW, NOW]
                assert [value.split(":", maxsplit=1)[0] for value in repository.events] == [
                    "department",
                    "select",
                    "candidates",
                    "select",
                ]
                assert detail.queue.criteria_snapshot.suggested_new_acquisition == 5

                item_page = await service.list_queue_items(
                    manager,
                    detail.queue.id,
                    RefreshQueueItemListQuery(offset=0, limit=50),
                )
                assert item_page.total == 1
                assert item_page.items[0].identity_snapshot.followers_count == 1234
                frozen_name = item_page.items[0].identity_snapshot.account_name
                assert frozen_name is not None and frozen_name.startswith("  =")

                audit_before_gets = int(
                    await session.scalar(select(func.count()).select_from(AuditLog)) or 0
                )
                page = await service.list_queues(
                    manager,
                    RefreshQueueListQuery(offset=0, limit=50),
                )
                assert page.total == 1
                assert (await service.get_queue_detail(manager, detail.queue.id)).queue.id == (
                    detail.queue.id
                )
                assert int(
                    await session.scalar(select(func.count()).select_from(AuditLog)) or 0
                ) == (audit_before_gets)

                # The export must remain a pure projection of the frozen item,
                # even after a live public identity changes.
                account.account_name = "A changed live name"
                await session.commit()
                first_export = await service.export_queue(
                    manager,
                    detail.queue.id,
                    "127.0.0.1",
                    "refresh-service-test",
                )
                clock.advance()
                second_export = await service.export_queue(
                    manager,
                    detail.queue.id,
                    "127.0.0.1",
                    "refresh-service-test",
                )
                assert first_export.content == second_export.content
                assert first_export.filename == f"refresh-queue-{detail.queue.id}.csv"
                assert "A changed live name" not in first_export.content
                assert "'  =HYPERLINK" in first_export.content
                exported_queue = await session.get(RefreshQueue, detail.queue.id)
                assert exported_queue is not None
                first_exported_at = exported_queue.exported_at
                assert exported_queue.status is RefreshQueueStatus.EXPORTED
                assert first_exported_at is not None

                clock.advance()
                cancelled = await service.cancel_queue(
                    manager,
                    detail.queue.id,
                    "127.0.0.1",
                    "refresh-service-test",
                )
                assert cancelled.queue.status is RefreshQueueStatus.CANCELLED
                assert cancelled.summary.status_breakdown == {RefreshQueueItemStatus.CANCELLED: 1}
                persisted_item = await session.scalar(
                    select(RefreshQueueItem).where(RefreshQueueItem.queue_id == detail.queue.id)
                )
                assert persisted_item is not None
                assert persisted_item.status is RefreshQueueItemStatus.CANCELLED

                logs = list(await session.scalars(select(AuditLog)))
                assert sorted(log.action.value for log in logs) == sorted(
                    [
                        AuditAction.REFRESH_QUEUE_CREATED.value,
                        AuditAction.REFRESH_QUEUE_EXPORTED.value,
                        AuditAction.REFRESH_QUEUE_EXPORTED.value,
                        AuditAction.REFRESH_QUEUE_CANCELLED.value,
                    ]
                )
                assert all(log.operator_id == manager.operator.id for log in logs)
                assert all("identity_snapshot" not in (log.after or {}) for log in logs)

                viewer = AuthContext(
                    department=manager.department,
                    operator=manager.operator,
                    role=Role.VIEWER,
                    auth_session=manager.auth_session,
                )
                with pytest.raises(RefreshQueueError) as viewer_create:
                    await service.create_queue(
                        viewer,
                        _create_input(),
                        "127.0.0.1",
                        "refresh-service-test",
                    )
                assert viewer_create.value.status_code == 403

                with pytest.raises(RefreshQueueError) as safe_not_found:
                    await service.get_queue_detail(other, detail.queue.id)
                assert safe_not_found.value.status_code == 404
                assert safe_not_found.value.code == "REFRESH_QUEUE_NOT_FOUND"

                with pytest.raises(RefreshQueueError) as terminal_export:
                    await service.export_queue(
                        manager,
                        detail.queue.id,
                        "127.0.0.1",
                        "refresh-service-test",
                    )
                assert terminal_export.value.status_code == 409

                # Last because a failed mutating transaction deliberately rolls
                # back and expires ORM context objects in this shared test session.
                await session.refresh(manager.department)
                await session.refresh(manager.operator)
                with pytest.raises(RefreshQueueError) as cross_department:
                    await service.create_queue(
                        manager,
                        _create_input(department_id=other_department_id),
                        "127.0.0.1",
                        "refresh-service-test",
                    )
                assert cross_department.value.status_code == 403
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_create_rolls_back_header_items_and_audit_when_commit_flush_fails() -> None:
    class InjectedAuditFailure(RuntimeError):
        pass

    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                actor = await _seed_actor(session, name="rollback", role=Role.OPERATOR)
                assert actor.operator is not None
                _account, candidate = await _seed_candidate(session, actor.operator)
                await session.commit()
                service = RefreshQueueService(
                    session,
                    freshness_policy=FreshnessPolicy(),
                    clock=MutableClock(),
                    repository=FixedCandidateRepository(session, candidate),
                )

                def fail_audit_insert(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    _parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    if statement.lstrip().upper().startswith("INSERT INTO AUDIT_LOGS"):
                        raise InjectedAuditFailure("synthetic audit persistence failure")

                event.listen(engine.sync_engine, "before_cursor_execute", fail_audit_insert)
                try:
                    with pytest.raises(InjectedAuditFailure):
                        await service.create_queue(
                            actor,
                            _create_input(),
                            "127.0.0.1",
                            "refresh-service-test",
                        )
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", fail_audit_insert)

                assert (
                    int(await session.scalar(select(func.count()).select_from(RefreshQueue)) or 0)
                    == 0
                )
                assert (
                    int(
                        await session.scalar(select(func.count()).select_from(RefreshQueueItem))
                        or 0
                    )
                    == 0
                )
                assert (
                    int(await session.scalar(select(func.count()).select_from(AuditLog)) or 0) == 0
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
