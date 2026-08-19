"""Focused contract coverage for the bounded Phase 3A Today projection."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from uuid import UUID, uuid4

import pytest
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, Operator
from backend_core.auth.service import AuthContext
from backend_core.campaigns.errors import CampaignOutreachError
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.growth.enums import CampaignReviewMode, CampaignStatus, DuplicateHistoryPolicy
from backend_core.growth.models import Campaign, CampaignMember
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
    InfluencerPlatformAccount,
)
from backend_core.outreach.enums import (
    OutreachActorType,
    OutreachChannel,
    OutreachEventType,
    OutreachPriority,
    OutreachPrioritySource,
    OutreachTaskKind,
    OutreachTaskState,
)
from backend_core.outreach.models import OutreachEvent, OutreachTarget, OutreachTask
from backend_core.outreach.today import (
    TodayCursorCodec,
    TodayQuery,
    TodayService,
    next_business_day,
)
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SQLITE_TABLES = (
    "departments",
    "operators",
    "influencers",
    "influencer_platform_accounts",
    "influencer_contacts",
    "influencer_current_metrics",
    "campaigns",
    "campaign_members",
    "outreach_targets",
    "outreach_tasks",
    "outreach_events",
)


def async_test(function: object) -> object:
    """Run isolated async cases without making pytest's async plugin global."""

    @wraps(function)
    def wrapper() -> None:
        asyncio.run(function())  # type: ignore[operator]

    return wrapper


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


@dataclass(slots=True)
class TodayHarness:
    session: AsyncSession
    context: AuthContext
    department: Department
    other_department: Department
    operator_a: Operator
    operator_b: Operator
    operator_c: Operator
    other_operator: Operator
    campaign: Campaign
    other_campaign: Campaign
    clock: MutableClock


@dataclass(frozen=True, slots=True)
class InfluencerFixture:
    influencer: Influencer
    preferred_account: InfluencerPlatformAccount


def _auth_context(
    department: Department, operator: Operator, *, role: Role = Role.OPERATOR
) -> AuthContext:
    return AuthContext(
        department=department,
        operator=operator,
        role=role,
        auth_session=AuthSession(
            department_id=department.id,
            operator_id=operator.id,
            token_hash=uuid4().hex + uuid4().hex,
            csrf_token_hash=uuid4().hex + uuid4().hex,
            ip="127.0.0.1",
            user_agent="today-projection-test",
            expires_at=datetime(2026, 9, 1, tzinfo=UTC),
            revoked_at=None,
        ),
    )


@asynccontextmanager
async def today_harness(
    *,
    as_of: datetime = datetime(2026, 8, 18, 4, 0, tzinfo=UTC),
) -> AsyncIterator[TodayHarness]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        # These expression indexes deliberately use PostgreSQL JSONB syntax.
        # The projection itself is portable, so omit only the physical indexes
        # while assembling the isolated SQLite fixture.
        metrics_table = Base.metadata.tables["influencer_current_metrics"]
        metrics_indexes = set(metrics_table.indexes)
        metrics_table.indexes.clear()
        try:
            for table_name in SQLITE_TABLES:
                await connection.run_sync(
                    lambda sync_connection, name=table_name: Base.metadata.tables[name].create(
                        sync_connection
                    )
                )
        finally:
            metrics_table.indexes.update(metrics_indexes)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            department = Department(
                name=f"Today department {uuid4().hex}",
                password_hash="not-used",
                status=DepartmentStatus.ACTIVE,
                session_days=30,
            )
            other_department = Department(
                name=f"Today other department {uuid4().hex}",
                password_hash="not-used",
                status=DepartmentStatus.ACTIVE,
                session_days=30,
            )
            session.add_all((department, other_department))
            await session.flush()
            operator_a = Operator(
                department_id=department.id,
                name="Assigned A",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
            )
            operator_b = Operator(
                department_id=department.id,
                name="Campaign owner B",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
            )
            operator_c = Operator(
                department_id=department.id,
                name="Influencer owner C",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
            )
            other_operator = Operator(
                department_id=other_department.id,
                name="Other Department operator",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
            )
            session.add_all((operator_a, operator_b, operator_c, other_operator))
            await session.flush()
            campaign = _campaign(department.id, operator_b.id, operator_a.id, name="Today Campaign")
            other_campaign = _campaign(
                other_department.id,
                other_operator.id,
                other_operator.id,
                name="Other Today Campaign",
            )
            session.add_all((campaign, other_campaign))
            await session.commit()
            context = _auth_context(department, operator_a)
            yield TodayHarness(
                session=session,
                context=context,
                department=department,
                other_department=other_department,
                operator_a=operator_a,
                operator_b=operator_b,
                operator_c=operator_c,
                other_operator=other_operator,
                campaign=campaign,
                other_campaign=other_campaign,
                clock=MutableClock(as_of),
            )
    finally:
        await engine.dispose()


def _campaign(department_id: UUID, owner_id: UUID, creator_id: UUID, *, name: str) -> Campaign:
    return Campaign(
        department_id=department_id,
        owner_operator_id=owner_id,
        created_by_operator_id=creator_id,
        name=name,
        status=CampaignStatus.DRAFT,
        review_mode=CampaignReviewMode.FIRST_N,
        review_count=1,
        duplicate_history_policy=DuplicateHistoryPolicy.ALLOW_WITH_WARNING,
        duplicate_window_days=None,
        version=1,
    )


async def seed_influencer(
    harness: TodayHarness,
    *,
    source_tags: list[str] | None = None,
    owner_operator_id: UUID | None = None,
    add_contact: bool = False,
) -> InfluencerFixture:
    influencer = Influencer(
        display_name=f"Today influencer {uuid4().hex}",
        owner_operator_id=owner_operator_id or harness.operator_c.id,
        crm_stage=CRMStage.TO_DEVELOP,
        status=InfluencerStatus.ACTIVE,
        deleted_at=None,
    )
    harness.session.add(influencer)
    await harness.session.flush()
    account = InfluencerPlatformAccount(
        influencer_id=influencer.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id=uuid4().hex,
        account_name=f"Preferred {uuid4().hex}",
        account_handle=f"preferred-{uuid4().hex[:8]}",
        source=DataSource.MANUAL,
        is_active=True,
        source_tags=source_tags,
    )
    harness.session.add(account)
    if add_contact:
        now = datetime(2026, 8, 1, tzinfo=UTC)
        harness.session.add(
            InfluencerContact(
                influencer_id=influencer.id,
                platform_account_id=None,
                type=ContactType.EMAIL,
                value="contact@example.invalid",
                normalized_value=f"contact-{uuid4().hex}@example.invalid",
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
        )
    await harness.session.flush()
    return InfluencerFixture(influencer=influencer, preferred_account=account)


async def seed_task(
    harness: TodayHarness,
    fixture: InfluencerFixture,
    *,
    due_at: datetime,
    assigned_operator_id: UUID | None = None,
    priority: OutreachPriority = OutreachPriority.NORMAL,
    campaign: Campaign | None = None,
    department: Department | None = None,
    added_by_operator_id: UUID | None = None,
    task_id: UUID | None = None,
) -> OutreachTask:
    campaign = campaign or harness.campaign
    department = department or harness.department
    adder = added_by_operator_id or harness.operator_a.id
    member = CampaignMember(
        department_id=department.id,
        campaign_id=campaign.id,
        influencer_id=fixture.influencer.id,
        preferred_platform_account_id=fixture.preferred_account.id,
        source_pool_run_id=None,
        added_by_operator_id=adder,
        removed_at=None,
        version=1,
    )
    harness.session.add(member)
    await harness.session.flush()
    target = OutreachTarget(
        department_id=department.id,
        campaign_id=campaign.id,
        member_id=member.id,
        influencer_id=fixture.influencer.id,
        channel=OutreachChannel.MANUAL,
        contact_id=None,
        platform_account_id=fixture.preferred_account.id,
        version=1,
    )
    harness.session.add(target)
    await harness.session.flush()
    task = OutreachTask(
        id=task_id or uuid4(),
        department_id=department.id,
        campaign_id=campaign.id,
        outreach_target_id=target.id,
        kind=OutreachTaskKind.FIRST_TOUCH,
        step_key=f"today-{uuid4().hex}",
        state=OutreachTaskState.READY,
        priority=priority,
        priority_source=(
            OutreachPrioritySource.MANUAL
            if priority is OutreachPriority.HIGH
            else OutreachPrioritySource.DEFAULT
        ),
        priority_reason_codes=None,
        assigned_operator_id=assigned_operator_id,
        due_at=due_at,
        template_version_id=None,
        version=1,
        create_idempotency_key=f"today-{uuid4().hex}",
        create_request_hash="0" * 64,
    )
    harness.session.add(task)
    await harness.session.flush()
    return task


async def seed_metrics(
    harness: TodayHarness,
    fixture: InfluencerFixture,
    *,
    source: DataSource,
    metrics: dict[str, object],
) -> None:
    harness.session.add(
        InfluencerCurrentMetrics(
            influencer_id=fixture.influencer.id,
            platform_account_id=fixture.preferred_account.id,
            source=source,
            source_updated_at=None,
            metrics=metrics,
            metrics_hash=uuid4().hex + uuid4().hex,
            last_import_job_id=uuid4(),
            last_import_row_id=uuid4(),
        )
    )
    await harness.session.flush()


def today_service(harness: TodayHarness) -> TodayService:
    return TodayService(harness.session, clock=harness.clock)


@pytest.mark.parametrize(
    ("as_of", "expected"),
    (
        (datetime(2026, 8, 17, 4, tzinfo=UTC), datetime(2026, 8, 17, 16, tzinfo=UTC)),
        (datetime(2026, 8, 18, 4, tzinfo=UTC), datetime(2026, 8, 18, 16, tzinfo=UTC)),
        (datetime(2026, 8, 21, 7, tzinfo=UTC), datetime(2026, 8, 23, 16, tzinfo=UTC)),
        (datetime(2026, 8, 22, 7, tzinfo=UTC), datetime(2026, 8, 23, 16, tzinfo=UTC)),
        (datetime(2026, 8, 23, 7, tzinfo=UTC), datetime(2026, 8, 23, 16, tzinfo=UTC)),
    ),
)
def test_next_business_day_is_weekday_only_shanghai_boundary(
    as_of: datetime,
    expected: datetime,
) -> None:
    assert next_business_day(as_of).astimezone(UTC) == expected


@async_test
async def test_today_owner_is_task_assignment_and_scope_precedes_filter() -> None:
    async with today_harness() as harness:
        fixture = await seed_influencer(harness)
        due_at = datetime(2026, 8, 18, 12, tzinfo=UTC)
        task = await seed_task(
            harness,
            fixture,
            due_at=due_at,
            assigned_operator_id=harness.operator_a.id,
        )
        other_fixture = await seed_influencer(harness)
        await seed_task(
            harness,
            other_fixture,
            due_at=due_at,
            assigned_operator_id=harness.other_operator.id,
            campaign=harness.other_campaign,
            department=harness.other_department,
            added_by_operator_id=harness.other_operator.id,
        )
        await harness.session.commit()
        service = today_service(harness)

        assigned = await service.list_today(
            harness.context,
            TodayQuery(owner_operator_id=harness.operator_a.id),
        )
        assert [item.task_id for item in assigned.items] == [task.id]
        assert not (
            await service.list_today(
                harness.context,
                TodayQuery(owner_operator_id=harness.operator_b.id),
            )
        ).items
        assert not (
            await service.list_today(
                harness.context,
                TodayQuery(owner_operator_id=harness.operator_c.id),
            )
        ).items

        task.assigned_operator_id = harness.operator_b.id
        await harness.session.commit()
        reassigned = await service.list_today(
            harness.context,
            TodayQuery(owner_operator_id=harness.operator_b.id),
        )
        assert [item.task_id for item in reassigned.items] == [task.id]

        viewer_context = _auth_context(harness.department, harness.operator_a, role=Role.VIEWER)
        scoped = await service.list_today(
            viewer_context,
            TodayQuery(owner_operator_id=harness.operator_b.id),
        )
        assert [item.task_id for item in scoped.items] == [task.id]


@async_test
async def test_today_track_uses_only_exact_preferred_account_tags() -> None:
    async with today_harness() as harness:
        matching = await seed_influencer(harness, source_tags=["beauty", "母婴"])
        non_matching = await seed_influencer(harness, source_tags=["beauty-care"])
        another_account = await seed_influencer(harness, source_tags=["fashion"])
        harness.session.add(
            InfluencerPlatformAccount(
                influencer_id=another_account.influencer.id,
                platform=Platform.XIAOHONGSHU,
                platform_account_id=uuid4().hex,
                account_name="Non-preferred beauty account",
                source=DataSource.MANUAL,
                is_active=True,
                source_tags=["beauty"],
            )
        )
        missing_tags = await seed_influencer(harness, source_tags=None)
        due_at = datetime(2026, 8, 18, 12, tzinfo=UTC)
        matching_task = await seed_task(harness, matching, due_at=due_at)
        await seed_task(harness, non_matching, due_at=due_at)
        await seed_task(harness, another_account, due_at=due_at)
        await seed_task(harness, missing_tags, due_at=due_at)
        await harness.session.commit()

        page = await today_service(harness).list_today(harness.context, TodayQuery(track="beauty"))
        assert [item.task_id for item in page.items] == [matching_task.id]
        assert not (
            await today_service(harness).list_today(harness.context, TodayQuery(track="beaut"))
        ).items


@async_test
async def test_today_followers_use_only_preferred_huitun_metrics_and_bounds() -> None:
    async with today_harness() as harness:
        valid = await seed_influencer(harness, source_tags=["beauty"])
        absent_huitun = await seed_influencer(harness, source_tags=["beauty"])
        missing_follower = await seed_influencer(harness, source_tags=["beauty"])
        malformed = await seed_influencer(harness, source_tags=["beauty"])
        await seed_metrics(
            harness, valid, source=DataSource.HUITUN, metrics={"followers_count": 1200}
        )
        await seed_metrics(
            harness,
            absent_huitun,
            source=DataSource.GENERIC,
            metrics={"followers_count": 9999},
        )
        await seed_metrics(harness, missing_follower, source=DataSource.HUITUN, metrics={})
        await seed_metrics(
            harness,
            malformed,
            source=DataSource.HUITUN,
            metrics={"followers_count": "not-an-integer"},
        )
        due_at = datetime(2026, 8, 18, 12, tzinfo=UTC)
        tasks = {
            "valid": await seed_task(harness, valid, due_at=due_at),
            "absent_huitun": await seed_task(harness, absent_huitun, due_at=due_at),
            "missing_follower": await seed_task(harness, missing_follower, due_at=due_at),
            "malformed": await seed_task(harness, malformed, due_at=due_at),
        }
        await harness.session.commit()
        service = today_service(harness)

        no_bounds = await service.list_today(harness.context, TodayQuery(track="beauty"))
        followers = {
            item.task_id: item.preferred_platform_account.followers_count
            for item in no_bounds.items
        }
        assert followers[tasks["valid"].id] == 1200
        assert followers[tasks["absent_huitun"].id] is None
        assert followers[tasks["missing_follower"].id] is None
        assert followers[tasks["malformed"].id] is None

        lower = await service.list_today(
            harness.context,
            TodayQuery(track="beauty", followers_min=1200),
        )
        assert [item.task_id for item in lower.items] == [tasks["valid"].id]
        upper = await service.list_today(
            harness.context,
            TodayQuery(track="beauty", followers_max=1200),
        )
        assert [item.task_id for item in upper.items] == [tasks["valid"].id]
        assert not (
            await service.list_today(
                harness.context,
                TodayQuery(track="beauty", followers_max=1199),
            )
        ).items


@async_test
async def test_today_uses_one_projection_query_and_current_global_history_warning() -> None:
    async with today_harness() as harness:
        fixture = await seed_influencer(harness, add_contact=True)
        current_task = await seed_task(
            harness,
            fixture,
            due_at=datetime(2026, 8, 18, 12, tzinfo=UTC),
        )
        historical_member = CampaignMember(
            department_id=harness.other_department.id,
            campaign_id=harness.other_campaign.id,
            influencer_id=fixture.influencer.id,
            preferred_platform_account_id=fixture.preferred_account.id,
            source_pool_run_id=None,
            added_by_operator_id=harness.other_operator.id,
            removed_at=None,
            version=1,
        )
        harness.session.add(historical_member)
        await harness.session.flush()
        historical_target = OutreachTarget(
            department_id=harness.other_department.id,
            campaign_id=harness.other_campaign.id,
            member_id=historical_member.id,
            influencer_id=fixture.influencer.id,
            channel=OutreachChannel.MANUAL,
            contact_id=None,
            platform_account_id=fixture.preferred_account.id,
            version=1,
        )
        harness.session.add(historical_target)
        await harness.session.flush()
        historical_task = OutreachTask(
            department_id=harness.other_department.id,
            campaign_id=harness.other_campaign.id,
            outreach_target_id=historical_target.id,
            kind=OutreachTaskKind.FIRST_TOUCH,
            step_key=f"history-{uuid4().hex}",
            state=OutreachTaskState.SENT,
            priority=OutreachPriority.NORMAL,
            priority_source=OutreachPrioritySource.DEFAULT,
            priority_reason_codes=None,
            assigned_operator_id=harness.other_operator.id,
            due_at=datetime(2026, 8, 1, tzinfo=UTC),
            template_version_id=None,
            version=2,
            create_idempotency_key=f"history-{uuid4().hex}",
            create_request_hash="0" * 64,
        )
        harness.session.add(historical_task)
        await harness.session.flush()
        sent_at = datetime(2026, 8, 10, 10, tzinfo=UTC)
        harness.session.add(
            OutreachEvent(
                department_id=harness.other_department.id,
                campaign_id=harness.other_campaign.id,
                influencer_id=fixture.influencer.id,
                task_id=historical_task.id,
                channel=OutreachChannel.MANUAL,
                event_type=OutreachEventType.OUTREACH_SENT,
                actor_type=OutreachActorType.OPERATOR,
                actor_operator_id=harness.other_operator.id,
                occurred_at=sent_at,
                from_state=OutreachTaskState.READY,
                to_state=OutreachTaskState.SENT,
                reason_code=None,
                idempotency_key=f"sent-{uuid4().hex}",
                request_hash="0" * 64,
                redacted_target_snapshot=None,
                redacted_message_snapshot=None,
                event_metadata={"task_version": 2},
            )
        )
        await harness.session.commit()
        statement_count = 0

        def count_statements(*_args: object, **_kwargs: object) -> None:
            nonlocal statement_count
            statement_count += 1

        engine = harness.session.get_bind()
        event.listen(engine, "before_cursor_execute", count_statements)
        try:
            page = await today_service(harness).list_today(harness.context, TodayQuery())
        finally:
            event.remove(
                engine,
                "before_cursor_execute",
                count_statements,
            )
        assert statement_count == 1
        assert [item.task_id for item in page.items] == [current_task.id]
        assert page.items[0].has_contact is True
        assert page.items[0].has_email is True
        warning = page.items[0].history_warning
        assert warning is not None
        assert warning.channel is OutreachChannel.MANUAL
        assert warning.last_sent_at == sent_at
        assert set(warning.model_dump()) == {"channel", "last_sent_at"}


@async_test
async def test_today_boundary_business_date_and_cursor_contract() -> None:
    friday_as_of = datetime(2026, 8, 21, 7, tzinfo=UTC)
    async with today_harness(as_of=friday_as_of) as harness:
        fixture_before = await seed_influencer(harness)
        fixture_at = await seed_influencer(harness)
        fixture_after = await seed_influencer(harness)
        boundary = next_business_day(friday_as_of).astimezone(UTC)
        before = await seed_task(
            harness,
            fixture_before,
            due_at=boundary - timedelta(microseconds=1),
            task_id=UUID(int=1),
        )
        await seed_task(harness, fixture_at, due_at=boundary, task_id=UUID(int=2))
        await seed_task(
            harness,
            fixture_after,
            due_at=boundary + timedelta(microseconds=1),
            task_id=UUID(int=3),
        )
        await harness.session.commit()
        service = today_service(harness)
        friday = await service.list_today(harness.context, TodayQuery(limit=1))
        assert friday.business_date.isoformat() == "2026-08-21"
        assert [item.task_id for item in friday.items] == [before.id]

        harness.clock.current = datetime(2026, 8, 22, 7, tzinfo=UTC)
        saturday = await service.list_today(harness.context, TodayQuery())
        assert saturday.business_date.isoformat() == "2026-08-22"
        harness.clock.current = datetime(2026, 8, 23, 7, tzinfo=UTC)
        sunday = await service.list_today(harness.context, TodayQuery())
        assert sunday.business_date.isoformat() == "2026-08-23"

        # A second eligible row makes a token, and a different normalized query
        # or Department is rejected before another projection is issued.
        next_fixture = await seed_influencer(harness)
        await seed_task(
            harness,
            next_fixture,
            due_at=boundary - timedelta(microseconds=2),
            task_id=UUID(int=4),
        )
        await harness.session.commit()
        first_page = await service.list_today(harness.context, TodayQuery(limit=1))
        assert first_page.next_cursor is not None
        second_page = await service.list_today(
            harness.context,
            TodayQuery(limit=1, cursor=first_page.next_cursor),
        )
        assert [item.task_id for item in second_page.items] == [before.id]
        with pytest.raises(CampaignOutreachError) as changed_query:
            await service.list_today(
                harness.context,
                TodayQuery(limit=2, cursor=first_page.next_cursor),
            )
        assert (changed_query.value.status_code, changed_query.value.code) == (
            409,
            "CURSOR_MISMATCH",
        )
        other_context = _auth_context(harness.other_department, harness.other_operator)
        with pytest.raises(CampaignOutreachError) as changed_scope:
            await service.list_today(
                other_context,
                TodayQuery(limit=1, cursor=first_page.next_cursor),
            )
        assert (changed_scope.value.status_code, changed_scope.value.code) == (
            409,
            "CURSOR_MISMATCH",
        )


def test_today_cursor_codec_rejects_tampering_and_hashes_normalized_defaults() -> None:
    department_id = UUID(int=11)
    codec = TodayCursorCodec()
    assert codec.query_hash(TodayQuery(), department_id) == codec.query_hash(
        TodayQuery(limit=50),
        department_id,
    )
    token = codec.encode(
        priority_rank=1,
        due_at=datetime(2026, 8, 18, 16, tzinfo=UTC),
        task_id=UUID(int=12),
        query_hash=codec.query_hash(TodayQuery(), department_id),
        department_id=department_id,
    )
    with pytest.raises(CampaignOutreachError) as malformed:
        codec.decode("not-a-cursor")
    assert (malformed.value.status_code, malformed.value.code) == (422, "CURSOR_INVALID")
    with pytest.raises(CampaignOutreachError) as tampered:
        codec.decode(f"{token[:-1]}x")
    assert (tampered.value.status_code, tampered.value.code) == (422, "CURSOR_INVALID")
