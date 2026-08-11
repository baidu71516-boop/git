"""Portable unit coverage for the Phase 1C read-only influencer repository."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, Operator
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
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
from backend_core.influencers.repository import InfluencerRepository
from backend_core.influencers.schemas import InfluencerListQuery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

NOW = datetime(2026, 8, 11, 4, 0, tzinfo=UTC)


@asynccontextmanager
async def database_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def add_operator(
    session: AsyncSession,
    *,
    name: str,
    status: OperatorStatus = OperatorStatus.ACTIVE,
) -> Operator:
    department = Department(
        name=f"部门-{name}-{uuid4().hex}",
        password_hash="not-used-by-repository-tests",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name=name,
        role=Role.OPERATOR,
        status=status,
    )
    session.add(operator)
    await session.flush()
    return operator


async def add_influencer(
    session: AsyncSession,
    *,
    name: str,
    owner_id: UUID | None = None,
    crm_stage: CRMStage = CRMStage.TO_DEVELOP,
    status: InfluencerStatus = InfluencerStatus.ACTIVE,
    deleted_at: datetime | None = None,
    influencer_id: UUID | None = None,
    created_at: datetime = NOW,
) -> Influencer:
    influencer = Influencer(
        display_name=name,
        owner_operator_id=owner_id,
        crm_stage=crm_stage,
        status=status,
        deleted_at=deleted_at,
        created_at=created_at,
        updated_at=created_at,
    )
    if influencer_id is not None:
        influencer.id = influencer_id
    session.add(influencer)
    await session.flush()
    return influencer


async def add_account(
    session: AsyncSession,
    influencer: Influencer,
    *,
    name: str,
    active: bool = True,
    tags: list[str] | None = None,
    handle: str | None = None,
    bio: str | None = None,
    mcn_name: str | None = None,
    account_id: UUID | None = None,
) -> InfluencerPlatformAccount:
    identity = uuid4().hex
    account = InfluencerPlatformAccount(
        influencer_id=influencer.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id=identity,
        account_name=name,
        account_handle=handle,
        profile_url=f"https://example.invalid/profile/{identity}",
        normalized_profile_url=f"https://example.invalid/profile/{identity}",
        source=DataSource.HUITUN,
        is_active=active,
        source_tags=tags,
        bio=bio,
        mcn_name=mcn_name,
    )
    if account_id is not None:
        account.id = account_id
    session.add(account)
    await session.flush()
    return account


async def add_contact(
    session: AsyncSession,
    influencer: Influencer,
    *,
    value: str,
    account: InfluencerPlatformAccount | None = None,
    contact_type: ContactType = ContactType.EMAIL,
    current: bool = True,
    duplicate: bool = False,
) -> InfluencerContact:
    contact = InfluencerContact(
        influencer_id=influencer.id,
        platform_account_id=account.id if account is not None else None,
        type=contact_type,
        value=value,
        normalized_value=value.casefold(),
        source=DataSource.MANUAL,
        validation_status=ContactValidationStatus.UNVERIFIED,
        is_current=current,
        possible_duplicate_contact=duplicate,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    session.add(contact)
    await session.flush()
    return contact


async def add_metrics(
    session: AsyncSession,
    influencer: Influencer,
    account: InfluencerPlatformAccount,
    *,
    followers: object,
    source: DataSource = DataSource.HUITUN,
) -> InfluencerCurrentMetrics:
    metrics = InfluencerCurrentMetrics(
        influencer_id=influencer.id,
        platform_account_id=account.id,
        source=source,
        source_updated_at=NOW,
        metrics={"followers_count": followers},
        metrics_hash=uuid4().hex * 2,
        last_import_job_id=uuid4(),
        last_import_row_id=uuid4(),
    )
    session.add(metrics)
    await session.flush()
    return metrics


async def add_source_state(
    session: AsyncSession,
    influencer: Influencer,
    account: InfluencerPlatformAccount,
    *,
    tags: list[str],
    source: DataSource = DataSource.HUITUN,
) -> InfluencerSourceState:
    state = InfluencerSourceState(
        influencer_id=influencer.id,
        platform_account_id=account.id,
        source=source,
        source_updated_at=NOW,
        source_data={"creator_tags": tags},
        source_data_hash=uuid4().hex * 2,
        state_version=1,
        last_import_job_id=uuid4(),
        last_import_row_id=uuid4(),
    )
    session.add(state)
    await session.flush()
    return state


async def add_source_identity(
    session: AsyncSession,
    account: InfluencerPlatformAccount,
    *,
    source: DataSource = DataSource.HUITUN,
) -> PlatformAccountSourceIdentity:
    identity = PlatformAccountSourceIdentity(
        platform_account_id=account.id,
        platform=account.platform,
        source=source,
        external_account_id=uuid4().hex,
        first_import_job_id=uuid4(),
        first_import_row_id=uuid4(),
        last_import_job_id=uuid4(),
        last_import_row_id=uuid4(),
    )
    session.add(identity)
    await session.flush()
    return identity


async def add_snapshot(
    session: AsyncSession,
    influencer: Influencer,
    account: InfluencerPlatformAccount,
    *,
    snapshot_id: UUID,
    captured_at: datetime,
) -> InfluencerMetricSnapshot:
    snapshot = InfluencerMetricSnapshot(
        id=snapshot_id,
        influencer_id=influencer.id,
        platform_account_id=account.id,
        source=DataSource.HUITUN,
        source_updated_at=captured_at,
        import_job_id=uuid4(),
        import_row_id=uuid4(),
        captured_at=captured_at,
        metrics={"followers_count": 100},
        metrics_hash=uuid4().hex * 2,
        snapshot_key=uuid4().hex * 2,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


def ids(records: list[object]) -> list[UUID]:
    return [record.influencer.id for record in records]  # type: ignore[attr-defined]


def test_list_is_subject_unique_with_exact_total_stable_order_and_pages() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            shared_time = NOW - timedelta(hours=1)
            lower_id = UUID("00000000-0000-0000-0000-000000000001")
            higher_id = UUID("00000000-0000-0000-0000-000000000002")
            lower = await add_influencer(
                session,
                name="lower",
                influencer_id=lower_id,
                created_at=shared_time,
            )
            higher = await add_influencer(
                session,
                name="higher",
                influencer_id=higher_id,
                created_at=shared_time,
            )
            newest = await add_influencer(session, name="newest", created_at=NOW)
            first_active = await add_account(
                session,
                lower,
                name="z-account",
                account_id=UUID("00000000-0000-0000-0000-000000000012"),
            )
            second_active = await add_account(
                session,
                lower,
                name="a-account",
                account_id=UUID("00000000-0000-0000-0000-000000000011"),
            )
            inactive = await add_account(session, lower, name="inactive", active=False)
            await add_metrics(session, lower, first_active, followers=10)
            await add_metrics(session, lower, second_active, followers=20)
            await add_metrics(session, lower, inactive, followers=999)
            await add_contact(session, lower, value="one@example.invalid", duplicate=True)
            await add_contact(
                session,
                lower,
                value="00000000000",
                contact_type=ContactType.PHONE,
            )
            await add_contact(session, lower, value="old@example.invalid", current=False)
            await add_account(session, higher, name="higher-account")
            await add_account(session, newest, name="newest-account")
            disabled = await add_influencer(
                session,
                name="disabled",
                status=InfluencerStatus.DISABLED,
            )
            deleted = await add_influencer(session, name="deleted", deleted_at=NOW)
            await add_account(session, disabled, name="disabled-account")
            await add_account(session, deleted, name="deleted-account")
            repository = InfluencerRepository(session)

            first_page, total = await repository.list_influencers(
                InfluencerListQuery(page=1, page_size=2)
            )
            second_page, second_total = await repository.list_influencers(
                InfluencerListQuery(page=2, page_size=2)
            )
            beyond, beyond_total = await repository.list_influencers(
                InfluencerListQuery(page=3, page_size=2)
            )

            assert total == second_total == beyond_total == 3
            assert ids(first_page) == [newest.id, higher_id]
            assert ids(second_page) == [lower_id]
            assert beyond == []
            lower_record = second_page[0]
            assert [account.id for account in lower_record.platform_accounts] == [
                second_active.id,
                first_active.id,
            ]
            assert len(lower_record.current_metrics) == 2
            assert len(lower_record.current_contacts) == 2
            assert any(
                contact.possible_duplicate_contact for contact in lower_record.current_contacts
            )

    asyncio.run(scenario())


def test_search_uses_only_subject_and_active_account_names_with_literal_wildcards() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            body = await add_influencer(session, name="Alpha%_\\Creator")
            await add_account(session, body, name="ordinary")
            account_match = await add_influencer(session, name="unrelated")
            await add_account(session, account_match, name="Active Account Name")
            inactive_only = await add_influencer(session, name="inactive-only")
            await add_account(session, inactive_only, name="Hidden Search", active=False)
            forbidden = await add_influencer(session, name="forbidden-fields")
            forbidden_account = await add_account(
                session,
                forbidden,
                name="ordinary-forbidden",
                handle="handle-secret",
                bio="bio-secret",
                mcn_name="mcn-secret",
            )
            await add_contact(session, forbidden, value="contact-secret")
            metrics = await add_metrics(session, forbidden, forbidden_account, followers=10)
            metrics.metrics["metrics-secret"] = "metrics-secret"
            controls = [
                await add_influencer(session, name="AlphaXXCreator"),
                await add_influencer(session, name="Alpha%X\\Creator"),
            ]
            for control in controls:
                await add_account(session, control, name=f"control-{control.id}")
            repository = InfluencerRepository(session)

            subject_records, _ = await repository.list_influencers(
                InfluencerListQuery(q="alpha%_\\creator")
            )
            account_records, _ = await repository.list_influencers(
                InfluencerListQuery(q="active account")
            )
            inactive_records, _ = await repository.list_influencers(
                InfluencerListQuery(q="Hidden Search")
            )

            assert ids(subject_records) == [body.id]
            assert ids(account_records) == [account_match.id]
            assert inactive_records == []
            for forbidden_query in [
                "handle-secret",
                "bio-secret",
                "mcn-secret",
                "contact-secret",
                "metrics-secret",
            ]:
                records, total = await repository.list_influencers(
                    InfluencerListQuery(q=forbidden_query)
                )
                assert records == []
                assert total == 0

    asyncio.run(scenario())


def test_tag_filter_is_exact_case_sensitive_active_account_projection_only() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            exact = await add_influencer(session, name="exact")
            exact_account = await add_account(
                session,
                exact,
                name="exact-account",
                tags=["Beauty美妆", "A%B"],
            )
            await add_source_state(session, exact, exact_account, tags=["SourceOnly"])
            inactive_only = await add_influencer(session, name="inactive")
            await add_account(
                session,
                inactive_only,
                name="inactive-account",
                active=False,
                tags=["Hidden"],
            )
            repository = InfluencerRepository(session)

            exact_records, exact_total = await repository.list_influencers(
                InfluencerListQuery(tag="Beauty美妆")
            )
            wrong_case, wrong_case_total = await repository.list_influencers(
                InfluencerListQuery(tag="beauty美妆")
            )
            substring, _ = await repository.list_influencers(InfluencerListQuery(tag="Beauty"))
            source_state_only, _ = await repository.list_influencers(
                InfluencerListQuery(tag="SourceOnly")
            )
            inactive, _ = await repository.list_influencers(InfluencerListQuery(tag="Hidden"))

            assert exact_total == 1
            assert ids(exact_records) == [exact.id]
            assert wrong_case == [] and wrong_case_total == 0
            assert substring == []
            assert source_state_only == []
            assert inactive == []

    asyncio.run(scenario())


def test_owner_crm_and_all_frozen_filters_are_combined_with_and() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            owner = await add_operator(session, name="目标负责人")
            other_owner = await add_operator(session, name="其他负责人")
            match = await add_influencer(
                session,
                name="Target Creator",
                owner_id=owner.id,
                crm_stage=CRMStage.HIGH_INTENT,
            )
            match_account = await add_account(
                session,
                match,
                name="Target Account",
                tags=["Beauty"],
            )
            await add_metrics(session, match, match_account, followers=300)

            wrong_owner = await add_influencer(
                session,
                name="Target Creator owner",
                owner_id=other_owner.id,
                crm_stage=CRMStage.HIGH_INTENT,
            )
            wrong_owner_account = await add_account(
                session, wrong_owner, name="Target Account owner", tags=["Beauty"]
            )
            await add_metrics(session, wrong_owner, wrong_owner_account, followers=300)
            wrong_stage = await add_influencer(
                session,
                name="Target Creator stage",
                owner_id=owner.id,
                crm_stage=CRMStage.TO_DEVELOP,
            )
            wrong_stage_account = await add_account(
                session, wrong_stage, name="Target Account stage", tags=["Beauty"]
            )
            await add_metrics(session, wrong_stage, wrong_stage_account, followers=300)
            no_owner = await add_influencer(
                session,
                name="Target Creator no owner",
                crm_stage=CRMStage.HIGH_INTENT,
            )
            no_owner_account = await add_account(
                session, no_owner, name="Target Account no owner", tags=["Beauty"]
            )
            await add_metrics(session, no_owner, no_owner_account, followers=300)
            repository = InfluencerRepository(session)

            owner_records, owner_total = await repository.list_influencers(
                InfluencerListQuery(owner_operator_id=owner.id)
            )
            stage_records, stage_total = await repository.list_influencers(
                InfluencerListQuery(crm_stage=CRMStage.HIGH_INTENT)
            )
            combined, combined_total = await repository.list_influencers(
                InfluencerListQuery(
                    q="Target",
                    tag="Beauty",
                    followers_min=250,
                    followers_max=350,
                    owner_operator_id=owner.id,
                    crm_stage=CRMStage.HIGH_INTENT,
                )
            )

            assert owner_total == 2
            assert set(ids(owner_records)) == {match.id, wrong_stage.id}
            assert stage_total == 3
            assert set(ids(stage_records)) == {match.id, wrong_owner.id, no_owner.id}
            assert combined_total == 1
            assert ids(combined) == [match.id]

    asyncio.run(scenario())


def test_sqlite_follower_range_uses_one_active_current_metrics_row() -> None:
    """Portable structure check; PostgreSQL JSONB types have a separate authority test."""

    async def scenario() -> None:
        async with database_session() as session:
            split = await add_influencer(session, name="split")
            low = await add_account(session, split, name="low")
            high = await add_account(session, split, name="high")
            await add_metrics(session, split, low, followers=50)
            await add_metrics(session, split, high, followers=200)
            same_row = await add_influencer(session, name="same-row")
            matching = await add_account(session, same_row, name="matching")
            await add_metrics(session, same_row, matching, followers=120)
            inactive_only = await add_influencer(session, name="inactive")
            inactive = await add_account(session, inactive_only, name="inactive", active=False)
            await add_metrics(session, inactive_only, inactive, followers=120)
            repository = InfluencerRepository(session)

            records, total = await repository.list_influencers(
                InfluencerListQuery(followers_min=100, followers_max=150)
            )

            assert total == 1
            assert ids(records) == [same_row.id]

    asyncio.run(scenario())


def test_detail_loads_only_current_contacts_and_active_account_read_graph() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            owner = await add_operator(
                session,
                name="停用但仍被引用",
                status=OperatorStatus.DISABLED,
            )
            influencer = await add_influencer(
                session,
                name="detail",
                owner_id=owner.id,
            )
            active = await add_account(session, influencer, name="active")
            inactive = await add_account(session, influencer, name="inactive", active=False)
            current_contact = await add_contact(
                session,
                influencer,
                value="current@example.invalid",
                account=active,
            )
            await add_contact(
                session,
                influencer,
                value="old@example.invalid",
                account=active,
                current=False,
            )
            active_state = await add_source_state(session, influencer, active, tags=["追溯标签"])
            await add_source_state(session, influencer, inactive, tags=["隐藏标签"])
            active_identity = await add_source_identity(session, active)
            await add_source_identity(session, inactive)
            active_metrics = await add_metrics(session, influencer, active, followers=100)
            await add_metrics(session, influencer, inactive, followers=999)
            disabled = await add_influencer(
                session,
                name="disabled",
                status=InfluencerStatus.DISABLED,
            )
            deleted = await add_influencer(session, name="deleted", deleted_at=NOW)
            repository = InfluencerRepository(session)

            detail = await repository.get_influencer_detail(influencer.id)

            assert detail is not None
            assert detail.owner is not None
            assert detail.owner.id == owner.id
            assert detail.owner.status == OperatorStatus.DISABLED
            assert [account.id for account in detail.platform_accounts] == [active.id]
            assert [contact.id for contact in detail.contacts] == [current_contact.id]
            assert [state.id for state in detail.source_states] == [active_state.id]
            assert [identity.id for identity in detail.source_identities] == [active_identity.id]
            assert [metrics.id for metrics in detail.current_metrics] == [active_metrics.id]
            assert await repository.get_influencer_detail(disabled.id) is None
            assert await repository.get_influencer_detail(deleted.id) is None
            assert await repository.get_influencer_detail(uuid4()) is None

    asyncio.run(scenario())


def test_filter_owner_options_are_referenced_deduplicated_cross_department_and_stable() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            disabled_owner = await add_operator(
                session,
                name="A Owner",
                status=OperatorStatus.DISABLED,
            )
            active_owner = await add_operator(session, name="B Owner")
            unreferenced = await add_operator(session, name="C Unreferenced")
            await add_influencer(session, name="one", owner_id=active_owner.id)
            await add_influencer(session, name="two", owner_id=active_owner.id)
            await add_influencer(session, name="three", owner_id=disabled_owner.id)
            await add_influencer(
                session,
                name="disabled subject",
                owner_id=unreferenced.id,
                status=InfluencerStatus.DISABLED,
            )

            owners = await InfluencerRepository(session).list_filter_option_owners()

            assert [owner.id for owner in owners] == [disabled_owner.id, active_owner.id]
            assert owners[0].status == OperatorStatus.DISABLED

    asyncio.run(scenario())


def test_filter_tag_options_use_only_visible_active_accounts_and_keep_long_raw_values() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            long_tag = "长" * 240
            visible = await add_influencer(session, name="visible")
            account = await add_account(
                session,
                visible,
                name="visible-account",
                tags=["Beauty", "Food", "Beauty", long_tag],
            )
            await add_source_state(session, visible, account, tags=["SourceStateOnly"])
            await add_account(
                session,
                visible,
                name="inactive-account",
                active=False,
                tags=["Inactive"],
            )
            disabled = await add_influencer(
                session,
                name="disabled",
                status=InfluencerStatus.DISABLED,
            )
            await add_account(session, disabled, name="disabled-account", tags=["Disabled"])

            tags = await InfluencerRepository(session).list_filter_option_tags()

            assert tags == ["Beauty", "Food", long_tag]

    asyncio.run(scenario())


def test_metric_snapshots_are_visible_only_for_active_subjects_and_page_stably() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            influencer = await add_influencer(session, name="snapshots")
            account = await add_account(session, influencer, name="snapshot-account")
            lower_id = UUID("00000000-0000-0000-0000-000000000011")
            higher_id = UUID("00000000-0000-0000-0000-000000000012")
            later_id = UUID("00000000-0000-0000-0000-000000000013")
            await add_snapshot(
                session,
                influencer,
                account,
                snapshot_id=lower_id,
                captured_at=NOW,
            )
            await add_snapshot(
                session,
                influencer,
                account,
                snapshot_id=higher_id,
                captured_at=NOW,
            )
            await add_snapshot(
                session,
                influencer,
                account,
                snapshot_id=later_id,
                captured_at=NOW + timedelta(hours=1),
            )
            disabled = await add_influencer(
                session,
                name="disabled snapshots",
                status=InfluencerStatus.DISABLED,
            )
            deleted = await add_influencer(session, name="deleted snapshots", deleted_at=NOW)
            repository = InfluencerRepository(session)

            first_page = await repository.list_metric_snapshots(influencer.id, page=1, page_size=2)
            second_page = await repository.list_metric_snapshots(influencer.id, page=2, page_size=2)
            beyond = await repository.list_metric_snapshots(influencer.id, page=3, page_size=2)

            assert first_page is not None and second_page is not None and beyond is not None
            assert [item.id for item in first_page[0]] == [later_id, higher_id]
            assert [item.id for item in second_page[0]] == [lower_id]
            assert beyond[0] == []
            assert first_page[1] == second_page[1] == beyond[1] == 3
            assert await repository.list_metric_snapshots(disabled.id, page=1, page_size=50) is None
            assert await repository.list_metric_snapshots(deleted.id, page=1, page_size=50) is None

    asyncio.run(scenario())
