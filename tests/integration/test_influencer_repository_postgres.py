"""PostgreSQL integration coverage for the Phase 1C influencer repository."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, Operator
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobStatus,
    ImportMatchType,
    ImportRowAction,
    ImportSourceType,
    StoredFileType,
)
from backend_core.imports.models import CollectionJob, ImportJob, ImportRow, StoredImportFile
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
)
from backend_core.influencers.repository import InfluencerRepository
from backend_core.influencers.schemas import InfluencerListQuery
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
    except ArgumentError as exc:
        raise RuntimeError("TEST_DATABASE_URL is invalid; refusing to connect") from exc
    database_name = (url.database or "").lower()
    if url.get_backend_name() != "postgresql" or "phase1b_test" not in database_name:
        raise RuntimeError(
            "Refusing unsafe PostgreSQL integration target: TEST_DATABASE_URL must use "
            "PostgreSQL and its database name must contain 'phase1b_test'"
        )
    return url.set(drivername="postgresql+psycopg")


TEST_DATABASE_URL = _gated_test_database_url()


@asynccontextmanager
async def _isolated_postgres() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    schema_name = f"phase1c_repository_{uuid4().hex}"
    admin_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    test_engine: AsyncEngine | None = None
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
        yield async_sessionmaker(test_engine, expire_on_commit=False)
    finally:
        if test_engine is not None:
            await test_engine.dispose()
        try:
            if schema_created:
                async with admin_engine.begin() as connection:
                    await connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
        finally:
            await admin_engine.dispose()


@dataclass
class _Provenance:
    operator: Operator
    job: ImportJob
    next_row_number: int = 2


async def _seed_provenance(session: AsyncSession) -> _Provenance:
    token = uuid4().hex
    department = Department(
        name=f"Phase 1C repository {token}",
        password_hash="not-a-real-password-hash",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name="Repository Owner",
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    session.add(operator)
    await session.flush()
    collection = CollectionJob(
        name="Phase 1C repository fixture",
        industry="测试",
        purpose="Repository PostgreSQL integration",
        target_action="读取",
        target_count=100,
        department_id=department.id,
        owner_operator_id=operator.id,
        source_type=ImportSourceType.GENERIC_CSV,
        status=CollectionJobStatus.COMPLETED,
    )
    digest = token * 2
    stored_file = StoredImportFile(
        sha256=digest,
        storage_key=f"repository/{token}.csv",
        size=1,
        detected_type=StoredFileType.CSV,
        detected_mime="text/csv",
        encoding="utf-8",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add_all([collection, stored_file])
    await session.flush()
    job = ImportJob(
        collection_job_id=collection.id,
        department_id=department.id,
        operator_id=operator.id,
        stored_file_id=stored_file.id,
        original_filename="repository-fixture.csv",
        mime_type="text/csv",
        file_size=1,
        sha256=digest,
        source_type=ImportSourceType.GENERIC_CSV,
        status=ImportJobStatus.COMPLETED,
        preview_revision=1,
        confirmed_revision=1,
        total_rows=100,
    )
    session.add(job)
    await session.flush()
    return _Provenance(operator=operator, job=job)


async def _new_import_row(session: AsyncSession, provenance: _Provenance) -> ImportRow:
    row_number = provenance.next_row_number
    provenance.next_row_number += 1
    row = ImportRow(
        import_job_id=provenance.job.id,
        row_number=row_number,
        raw_data={"fixture_row": row_number},
        normalized_data={"fixture_row": row_number},
        match_type=ImportMatchType.NONE,
        action=ImportRowAction.CREATE,
        warnings=[],
        errors=[],
        preview_revision=1,
        plan_hash=f"{row_number:064x}",
    )
    session.add(row)
    await session.flush()
    return row


async def _add_influencer(
    session: AsyncSession,
    *,
    name: str,
    owner_id: UUID | None = None,
    influencer_id: UUID | None = None,
    created_at: datetime | None = None,
    status: InfluencerStatus = InfluencerStatus.ACTIVE,
    deleted_at: datetime | None = None,
) -> Influencer:
    influencer = Influencer(
        display_name=name,
        owner_operator_id=owner_id,
        crm_stage=CRMStage.TO_DEVELOP,
        status=status,
        deleted_at=deleted_at,
    )
    if influencer_id is not None:
        influencer.id = influencer_id
    if created_at is not None:
        influencer.created_at = created_at
        influencer.updated_at = created_at
    session.add(influencer)
    await session.flush()
    return influencer


async def _add_account(
    session: AsyncSession,
    influencer: Influencer,
    *,
    account_name: str,
    is_active: bool = True,
    source_tags: list[str] | None = None,
) -> InfluencerPlatformAccount:
    identity = uuid4().hex
    account = InfluencerPlatformAccount(
        influencer_id=influencer.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id=identity,
        account_name=account_name,
        account_handle=f"handle-{identity}",
        profile_url=f"https://example.invalid/profile/{identity}",
        normalized_profile_url=f"https://example.invalid/profile/{identity}",
        source=DataSource.HUITUN,
        is_active=is_active,
        source_tags=source_tags,
    )
    session.add(account)
    await session.flush()
    return account


async def _add_metrics(
    session: AsyncSession,
    provenance: _Provenance,
    influencer: Influencer,
    account: InfluencerPlatformAccount,
    metrics: dict[str, object],
    *,
    source: DataSource = DataSource.HUITUN,
) -> InfluencerCurrentMetrics:
    row = await _new_import_row(session, provenance)
    current = InfluencerCurrentMetrics(
        influencer_id=influencer.id,
        platform_account_id=account.id,
        source=source,
        source_updated_at=datetime(2026, 8, 10, 12, tzinfo=UTC),
        metrics=metrics,
        metrics_hash=uuid4().hex * 2,
        last_import_job_id=provenance.job.id,
        last_import_row_id=row.id,
    )
    session.add(current)
    await session.flush()
    return current


def _record_ids(records: list[object]) -> list[UUID]:
    return [record.influencer.id for record in records]  # type: ignore[attr-defined]


def test_postgres_followers_jsonb_type_matrix_and_huge_integer_are_safe() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as factory, factory() as session:
            provenance = await _seed_provenance(session)
            fixtures: list[tuple[str, dict[str, object]]] = [
                ("zero", {"followers_count": 0}),
                ("hundred", {"followers_count": 100}),
                ("missing", {}),
                ("null", {"followers_count": None}),
                ("integer-string", {"followers_count": "100"}),
                ("decimal-string", {"followers_count": "1.0"}),
                ("decimal", {"followers_count": 1.5}),
                ("boolean", {"followers_count": True}),
                ("object", {"followers_count": {"value": 100}}),
                ("array", {"followers_count": [100]}),
                ("negative", {"followers_count": -1}),
                ("huge", {"followers_count": 2**80}),
            ]
            ids: dict[str, UUID] = {}
            for name, metrics in fixtures:
                influencer = await _add_influencer(session, name=name)
                account = await _add_account(session, influencer, account_name=f"{name}-account")
                await _add_metrics(session, provenance, influencer, account, metrics)
                ids[name] = influencer.id

            repository = InfluencerRepository(session)

            zero_records, zero_total = await repository.list_influencers(
                InfluencerListQuery(followers_min=0, followers_max=0)
            )
            assert zero_total == 1
            assert _record_ids(zero_records) == [ids["zero"]]

            hundred_records, hundred_total = await repository.list_influencers(
                InfluencerListQuery(followers_min=100, followers_max=100)
            )
            assert hundred_total == 1
            assert _record_ids(hundred_records) == [ids["hundred"]]

            huge_records, huge_total = await repository.list_influencers(
                InfluencerListQuery(followers_min=2**80, followers_max=2**80)
            )
            assert huge_total == 1
            assert _record_ids(huge_records) == [ids["huge"]]

            valid_records, valid_total = await repository.list_influencers(
                InfluencerListQuery(followers_min=0)
            )
            assert valid_total == 3
            assert set(_record_ids(valid_records)) == {
                ids["zero"],
                ids["hundred"],
                ids["huge"],
            }

    asyncio.run(scenario())


def test_postgres_follower_range_must_match_one_current_metrics_row() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as factory, factory() as session:
            provenance = await _seed_provenance(session)
            split = await _add_influencer(session, name="split-range")
            low_account = await _add_account(session, split, account_name="low")
            high_account = await _add_account(session, split, account_name="high")
            await _add_metrics(session, provenance, split, low_account, {"followers_count": 100})
            await _add_metrics(
                session,
                provenance,
                split,
                high_account,
                {"followers_count": 90_000},
            )

            inactive_only = await _add_influencer(session, name="inactive-only")
            inactive_account = await _add_account(
                session, inactive_only, account_name="inactive", is_active=False
            )
            await _add_metrics(
                session,
                provenance,
                inactive_only,
                inactive_account,
                {"followers_count": 30_000},
            )
            repository = InfluencerRepository(session)

            split_records, split_total = await repository.list_influencers(
                InfluencerListQuery(followers_min=10_000, followers_max=50_000)
            )
            assert split_records == []
            assert split_total == 0

            matching_account = await _add_account(session, split, account_name="matching")
            await _add_metrics(
                session,
                provenance,
                split,
                matching_account,
                {"followers_count": 30_000},
            )
            matching_records, matching_total = await repository.list_influencers(
                InfluencerListQuery(followers_min=10_000, followers_max=50_000)
            )

            assert matching_total == 1
            assert _record_ids(matching_records) == [split.id]

    asyncio.run(scenario())


def test_postgres_fanout_keeps_unique_influencers_and_exact_total() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as factory, factory() as session:
            provenance = await _seed_provenance(session)
            first = await _add_influencer(session, name="fanout", owner_id=provenance.operator.id)
            first_account = await _add_account(session, first, account_name="fanout-one")
            second_account = await _add_account(session, first, account_name="fanout-two")
            await _add_metrics(session, provenance, first, first_account, {"followers_count": 10})
            await _add_metrics(session, provenance, first, second_account, {"followers_count": 20})
            now = datetime.now(UTC)
            session.add_all(
                [
                    InfluencerContact(
                        influencer_id=first.id,
                        platform_account_id=first_account.id,
                        type=ContactType.EMAIL,
                        value="first@example.invalid",
                        normalized_value="first@example.invalid",
                        source=DataSource.MANUAL,
                        validation_status=ContactValidationStatus.UNVERIFIED,
                        is_current=True,
                        possible_duplicate_contact=False,
                        first_seen_at=now,
                        last_seen_at=now,
                    ),
                    InfluencerContact(
                        influencer_id=first.id,
                        platform_account_id=second_account.id,
                        type=ContactType.PHONE,
                        value="00000000000",
                        normalized_value="00000000000",
                        source=DataSource.MANUAL,
                        validation_status=ContactValidationStatus.UNVERIFIED,
                        is_current=True,
                        possible_duplicate_contact=True,
                        first_seen_at=now,
                        last_seen_at=now,
                    ),
                ]
            )
            second = await _add_influencer(session, name="plain")
            await _add_account(session, second, account_name="plain-account")
            await session.flush()

            records, total = await InfluencerRepository(session).list_influencers(
                InfluencerListQuery(page=1, page_size=100)
            )

            assert total == 2
            assert len(records) == 2
            assert len(set(_record_ids(records))) == 2
            fanout_record = next(record for record in records if record.influencer.id == first.id)
            assert len(fanout_record.platform_accounts) == 2
            assert len(fanout_record.current_metrics) == 2
            assert len(fanout_record.current_contacts) == 2

    asyncio.run(scenario())


def test_postgres_jsonb_tag_filter_is_exact_case_sensitive_and_active_only() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as factory, factory() as session:
            await _seed_provenance(session)
            visible = await _add_influencer(session, name="tagged")
            long_tag = "长" * 240
            await _add_account(
                session,
                visible,
                account_name="tagged-account",
                source_tags=["Beauty美妆", "A%B", long_tag],
            )
            await _add_account(
                session,
                visible,
                account_name="inactive-tagged-account",
                is_active=False,
                source_tags=["Hidden"],
            )
            disabled = await _add_influencer(
                session, name="disabled-tagged", status=InfluencerStatus.DISABLED
            )
            await _add_account(
                session,
                disabled,
                account_name="disabled-account",
                source_tags=["Invisible"],
            )
            repository = InfluencerRepository(session)

            exact, exact_total = await repository.list_influencers(
                InfluencerListQuery(tag="Beauty美妆")
            )
            assert exact_total == 1
            assert _record_ids(exact) == [visible.id]

            wrong_case, wrong_case_total = await repository.list_influencers(
                InfluencerListQuery(tag="beauty美妆")
            )
            assert wrong_case == []
            assert wrong_case_total == 0

            literal, literal_total = await repository.list_influencers(
                InfluencerListQuery(tag="A%B")
            )
            assert literal_total == 1
            assert _record_ids(literal) == [visible.id]

            hidden, hidden_total = await repository.list_influencers(
                InfluencerListQuery(tag="Hidden")
            )
            assert hidden == []
            assert hidden_total == 0

            assert await repository.list_filter_option_tags() == ["A%B", "Beauty美妆", long_tag]

    asyncio.run(scenario())


def test_postgres_search_escapes_ilike_wildcards_and_escape_character() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as factory, factory() as session:
            await _seed_provenance(session)
            percent = await _add_influencer(session, name="literal%name")
            await _add_account(session, percent, account_name="ordinary-percent")
            percent_control = await _add_influencer(session, name="literalXname")
            await _add_account(session, percent_control, account_name="percent-control")

            underscore = await _add_influencer(session, name="underscore-subject")
            await _add_account(session, underscore, account_name="under_score")
            underscore_control = await _add_influencer(session, name="underXscore")
            await _add_account(session, underscore_control, account_name="ordinary-underscore")

            slash = await _add_influencer(session, name=r"slash\name")
            await _add_account(session, slash, account_name="ordinary-slash")
            slash_control = await _add_influencer(session, name="slashXname")
            await _add_account(session, slash_control, account_name="slash-control")
            repository = InfluencerRepository(session)

            percent_records, percent_total = await repository.list_influencers(
                InfluencerListQuery(q="LITERAL%NAME")
            )
            assert percent_total == 1
            assert _record_ids(percent_records) == [percent.id]

            underscore_records, underscore_total = await repository.list_influencers(
                InfluencerListQuery(q="under_score")
            )
            assert underscore_total == 1
            assert _record_ids(underscore_records) == [underscore.id]

            slash_records, slash_total = await repository.list_influencers(
                InfluencerListQuery(q=r"slash\name")
            )
            assert slash_total == 1
            assert _record_ids(slash_records) == [slash.id]

    asyncio.run(scenario())


def test_postgres_default_order_uses_id_desc_as_created_at_tie_breaker() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as factory, factory() as session:
            await _seed_provenance(session)
            shared_created_at = datetime(2026, 8, 10, 12, tzinfo=UTC)
            lower_id = UUID("00000000-0000-0000-0000-000000000001")
            higher_id = UUID("00000000-0000-0000-0000-000000000002")
            await _add_influencer(
                session,
                name="lower",
                influencer_id=lower_id,
                created_at=shared_created_at,
            )
            await _add_influencer(
                session,
                name="higher",
                influencer_id=higher_id,
                created_at=shared_created_at,
            )

            records, total = await InfluencerRepository(session).list_influencers(
                InfluencerListQuery()
            )

            assert total == 2
            assert _record_ids(records) == [higher_id, lower_id]

    asyncio.run(scenario())


def test_postgres_metric_snapshot_order_and_pagination_are_deterministic() -> None:
    async def scenario() -> None:
        async with _isolated_postgres() as factory, factory() as session:
            provenance = await _seed_provenance(session)
            influencer = await _add_influencer(session, name="snapshots")
            account = await _add_account(session, influencer, account_name="snapshot-account")
            shared_captured_at = datetime(2026, 8, 10, 12, tzinfo=UTC)
            later_captured_at = shared_captured_at + timedelta(hours=1)
            lower_id = UUID("00000000-0000-0000-0000-000000000011")
            higher_id = UUID("00000000-0000-0000-0000-000000000012")
            later_id = UUID("00000000-0000-0000-0000-000000000013")
            for index, (snapshot_id, captured_at) in enumerate(
                [
                    (lower_id, shared_captured_at),
                    (higher_id, shared_captured_at),
                    (later_id, later_captured_at),
                ],
                start=1,
            ):
                row = await _new_import_row(session, provenance)
                session.add(
                    InfluencerMetricSnapshot(
                        id=snapshot_id,
                        influencer_id=influencer.id,
                        platform_account_id=account.id,
                        source=DataSource.HUITUN,
                        source_updated_at=shared_captured_at + timedelta(minutes=index),
                        import_job_id=provenance.job.id,
                        import_row_id=row.id,
                        captured_at=captured_at,
                        metrics={"followers_count": index},
                        metrics_hash=f"{1000 + index:064x}",
                        snapshot_key=f"{2000 + index:064x}",
                    )
                )
            await session.flush()
            repository = InfluencerRepository(session)

            first_page = await repository.list_metric_snapshots(influencer.id, page=1, page_size=2)
            assert first_page is not None
            first_items, first_total = first_page
            assert first_total == 3
            assert [item.id for item in first_items] == [later_id, higher_id]

            second_page = await repository.list_metric_snapshots(influencer.id, page=2, page_size=2)
            assert second_page is not None
            second_items, second_total = second_page
            assert second_total == 3
            assert [item.id for item in second_items] == [lower_id]

    asyncio.run(scenario())
