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
from backend_core.influencers.enums import (
    ContactFilter,
    ContactType,
    ContactValidationStatus,
    CRMStage,
    DataSource,
    InfluencerStatus,
    Notes7dFilter,
    Notes60dFilter,
    Platform,
)
from backend_core.influencers.freshness import FreshnessPolicy, FreshnessStatus
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)
from backend_core.influencers.repository import (
    AccountSourceFreshnessRecord,
    InfluencerListRecord,
    InfluencerRepository,
)
from backend_core.influencers.schemas import InfluencerListQuery
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

NOW = datetime(2026, 8, 11, 4, 0, tzinfo=UTC)


@asynccontextmanager
async def database_session(
    *,
    statements: list[str] | None = None,
) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    if statements is not None:

        def track_statement(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", track_statement)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        if statements is not None:
            event.remove(engine.sync_engine, "before_cursor_execute", track_statement)
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


async def add_import_context(
    session: AsyncSession,
) -> tuple[Operator, CollectionJob, StoredImportFile]:
    """Create real parent rows shared by committed-lineage fixtures."""

    operator = await add_operator(session, name=f"导入操作人-{uuid4().hex}")
    token = uuid4().hex
    collection = CollectionJob(
        name=f"Freshness fixture {token}",
        industry="测试",
        purpose="验证灰豚新鲜度 lineage",
        target_action="确认导入",
        target_count=100,
        department_id=operator.department_id,
        owner_operator_id=operator.id,
        source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
        status=CollectionJobStatus.COMPLETED,
    )
    stored_file = StoredImportFile(
        sha256=token * 2,
        storage_key=f"freshness/{token}.csv",
        size=1,
        detected_type=StoredFileType.CSV,
        detected_mime="text/csv",
        encoding="utf-8",
        expires_at=NOW + timedelta(days=1),
    )
    session.add_all([collection, stored_file])
    await session.flush()
    return operator, collection, stored_file


async def add_committed_lineage(
    session: AsyncSession,
    *,
    operator: Operator,
    collection: CollectionJob,
    stored_file: StoredImportFile,
    influencer: Influencer,
    account: InfluencerPlatformAccount,
    source_acquired_at: datetime | None,
    committed_at: datetime | None = NOW,
    committed_action: ImportRowAction | None = ImportRowAction.NO_CHANGE,
    source_type: ImportSourceType = ImportSourceType.MANUAL_HUITUN_EXPORT,
    job_status: ImportJobStatus = ImportJobStatus.COMPLETED,
    file_status: ImportJobFileStatus = ImportJobFileStatus.READY,
    job_preview_revision: int = 1,
    confirmed_revision: int | None = 1,
    row_preview_revision: int | None = None,
    confirmation_required: bool = False,
    legacy_single_file: bool = False,
) -> ImportRow:
    """Persist one realistic confirmed occurrence and its committed row."""

    resolved_row_revision = row_preview_revision or job_preview_revision
    job = ImportJob(
        collection_job_id=collection.id,
        department_id=operator.department_id,
        operator_id=operator.id,
        stored_file_id=stored_file.id if legacy_single_file else None,
        original_filename="legacy.csv" if legacy_single_file else None,
        mime_type="text/csv" if legacy_single_file else None,
        file_size=stored_file.size if legacy_single_file else None,
        sha256=stored_file.sha256 if legacy_single_file else None,
        source_type=source_type,
        status=job_status,
        preview_revision=job_preview_revision,
        confirmed_revision=confirmed_revision,
        confirmed_at=committed_at if confirmed_revision is not None else None,
        completed_at=committed_at if job_status is ImportJobStatus.COMPLETED else None,
        total_rows=1,
        valid_rows=1,
        created_rows=int(committed_action is ImportRowAction.CREATE),
        updated_rows=int(committed_action is ImportRowAction.UPDATE),
        no_change_rows=int(committed_action is ImportRowAction.NO_CHANGE),
    )
    session.add(job)
    await session.flush()

    if source_acquired_at is None:
        acquisition_origin = SourceAcquiredAtOrigin.LEGACY_UNKNOWN
    elif confirmation_required:
        acquisition_origin = SourceAcquiredAtOrigin.SERVER_DEFAULT
    else:
        acquisition_origin = SourceAcquiredAtOrigin.USER_CONFIRMED
    job_file = ImportJobFile(
        import_job_id=job.id,
        stored_file_id=stored_file.id,
        position=1,
        original_filename=f"occurrence-{job.id}.csv",
        declared_mime="text/csv",
        status=file_status,
        source_acquired_at=source_acquired_at,
        source_acquired_at_origin=acquisition_origin,
        source_acquired_at_confirmation_required=confirmation_required,
        raw_rows=1,
    )
    session.add(job_file)
    await session.flush()

    row = ImportRow(
        import_job_id=job.id,
        import_job_file_id=job_file.id,
        row_number=2,
        raw_data={"account_name": account.account_name},
        normalized_data={"account_name": account.account_name},
        matched_influencer_id=influencer.id,
        matched_platform_account_id=account.id,
        match_type=ImportMatchType.PLATFORM_ACCOUNT_ID,
        action=committed_action or ImportRowAction.NO_CHANGE,
        merge_plan={},
        warnings=[],
        errors=[],
        preview_revision=resolved_row_revision,
        plan_hash=uuid4().hex * 2,
        committed_action=committed_action,
        committed_at=committed_at,
    )
    session.add(row)
    await session.flush()
    return row


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
    notes_7d: object | None = None,
    notes_60d: object | None = None,
    source: DataSource = DataSource.HUITUN,
    import_job_id: UUID | None = None,
    import_row_id: UUID | None = None,
) -> InfluencerCurrentMetrics:
    values = {"followers_count": followers}
    if notes_7d is not None:
        values["notes_7d"] = notes_7d
    if notes_60d is not None:
        values["notes_60d"] = notes_60d
    metrics = InfluencerCurrentMetrics(
        influencer_id=influencer.id,
        platform_account_id=account.id,
        source=source,
        source_updated_at=NOW,
        metrics=values,
        metrics_hash=uuid4().hex * 2,
        last_import_job_id=import_job_id or uuid4(),
        last_import_row_id=import_row_id or uuid4(),
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


def freshness_by_account(
    record: InfluencerListRecord,
) -> dict[UUID, AccountSourceFreshnessRecord]:
    return {item.platform_account_id: item for item in record.huitun_freshness}


def test_huitun_observed_max_uses_only_successful_confirmed_lineage() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            operator, collection, stored_file = await add_import_context(session)
            influencer = await add_influencer(session, name="lineage gates")
            account = await add_account(session, influencer, name="lineage-account")

            allowed = (
                (ImportRowAction.CREATE, NOW - timedelta(days=12)),
                (ImportRowAction.UPDATE, NOW - timedelta(days=10)),
                (ImportRowAction.NO_CHANGE, NOW - timedelta(days=8)),
            )
            for action, observed_at in allowed:
                await add_committed_lineage(
                    session,
                    operator=operator,
                    collection=collection,
                    stored_file=stored_file,
                    influencer=influencer,
                    account=account,
                    source_acquired_at=observed_at,
                    committed_at=observed_at + timedelta(hours=1),
                    committed_action=action,
                )

            invalid_observed_at = NOW - timedelta(days=1)
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=influencer,
                account=account,
                source_acquired_at=invalid_observed_at,
                committed_at=NOW - timedelta(hours=8),
                job_status=ImportJobStatus.IMPORTING,
            )
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=influencer,
                account=account,
                source_acquired_at=invalid_observed_at + timedelta(hours=1),
                committed_at=NOW - timedelta(hours=7),
                source_type=ImportSourceType.GENERIC_CSV,
            )
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=influencer,
                account=account,
                source_acquired_at=invalid_observed_at + timedelta(hours=2),
                committed_at=NOW - timedelta(hours=6),
                file_status=ImportJobFileStatus.FAILED,
            )
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=influencer,
                account=account,
                source_acquired_at=invalid_observed_at + timedelta(hours=3),
                committed_at=NOW - timedelta(hours=5),
                confirmed_revision=None,
            )
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=influencer,
                account=account,
                source_acquired_at=invalid_observed_at + timedelta(hours=4),
                committed_at=NOW - timedelta(hours=4),
                job_preview_revision=2,
                confirmed_revision=2,
                row_preview_revision=1,
            )
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=influencer,
                account=account,
                source_acquired_at=invalid_observed_at + timedelta(hours=5),
                committed_at=NOW - timedelta(hours=3),
                committed_action=ImportRowAction.SKIP,
            )
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=influencer,
                account=account,
                source_acquired_at=invalid_observed_at + timedelta(hours=6),
                committed_at=NOW - timedelta(hours=2),
                committed_action=None,
            )
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=influencer,
                account=account,
                source_acquired_at=invalid_observed_at + timedelta(hours=7),
                committed_at=None,
            )
            await session.commit()

            records, total = await InfluencerRepository(session).list_influencers(
                InfluencerListQuery(),
                as_of=NOW,
                policy=FreshnessPolicy(),
            )

            assert total == 1
            assert ids(records) == [influencer.id]
            freshness = freshness_by_account(records[0])[account.id]
            assert freshness.source is DataSource.HUITUN
            assert freshness.last_observed_at == NOW - timedelta(days=8)
            assert freshness.last_imported_at == NOW - timedelta(days=8) + timedelta(hours=1)

    asyncio.run(scenario())


def test_legacy_committed_lineage_keeps_imported_fallback_without_observed_time() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            operator, collection, stored_file = await add_import_context(session)
            influencer = await add_influencer(session, name="legacy unknown")
            account = await add_account(session, influencer, name="legacy-account")
            imported_at = NOW - timedelta(days=2)
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=influencer,
                account=account,
                source_acquired_at=None,
                committed_at=imported_at,
                legacy_single_file=True,
            )
            await session.commit()
            repository = InfluencerRepository(session)

            records, total = await repository.list_influencers(
                InfluencerListQuery(), as_of=NOW, policy=FreshnessPolicy()
            )
            unknown, unknown_total = await repository.list_influencers(
                InfluencerListQuery(freshness_status=FreshnessStatus.UNKNOWN),
                as_of=NOW,
                policy=FreshnessPolicy(),
            )

            assert total == unknown_total == 1
            assert ids(records) == ids(unknown) == [influencer.id]
            freshness = freshness_by_account(records[0])[account.id]
            assert freshness.last_observed_at is None
            assert freshness.last_imported_at == imported_at

    asyncio.run(scenario())


def test_unconfirmed_or_missing_acquisition_never_becomes_observed_time() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            operator, collection, stored_file = await add_import_context(session)
            pending_subject = await add_influencer(session, name="pending confirmation")
            pending_account = await add_account(
                session, pending_subject, name="pending-confirmation-account"
            )
            missing_subject = await add_influencer(session, name="missing acquisition")
            missing_account = await add_account(
                session, missing_subject, name="missing-acquisition-account"
            )
            pending_imported_at = NOW - timedelta(hours=3)
            missing_imported_at = NOW - timedelta(hours=2)
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=pending_subject,
                account=pending_account,
                source_acquired_at=NOW - timedelta(hours=4),
                committed_at=pending_imported_at,
                confirmation_required=True,
            )
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=missing_subject,
                account=missing_account,
                source_acquired_at=None,
                committed_at=missing_imported_at,
            )
            await session.commit()

            records, total = await InfluencerRepository(session).list_influencers(
                InfluencerListQuery(), as_of=NOW, policy=FreshnessPolicy()
            )
            by_id = {record.influencer.id: record for record in records}

            assert total == 2
            pending = freshness_by_account(by_id[pending_subject.id])[pending_account.id]
            missing = freshness_by_account(by_id[missing_subject.id])[missing_account.id]
            assert pending.last_observed_at is None
            assert pending.last_imported_at == pending_imported_at
            assert missing.last_observed_at is None
            assert missing.last_imported_at == missing_imported_at

    asyncio.run(scenario())


def test_generic_evidence_cannot_advance_or_create_huitun_freshness() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            operator, collection, stored_file = await add_import_context(session)
            mixed_subject = await add_influencer(session, name="mixed sources")
            mixed_account = await add_account(session, mixed_subject, name="mixed-account")
            huitun_observed_at = NOW - timedelta(days=20)
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=mixed_subject,
                account=mixed_account,
                source_acquired_at=huitun_observed_at,
                committed_at=huitun_observed_at + timedelta(hours=1),
            )
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=mixed_subject,
                account=mixed_account,
                source_acquired_at=NOW - timedelta(hours=2),
                committed_at=NOW - timedelta(hours=1),
                source_type=ImportSourceType.GENERIC_CSV,
            )

            generic_subject = await add_influencer(session, name="generic only")
            generic_account = await add_account(
                session, generic_subject, name="generic-only-account"
            )
            await add_source_state(
                session,
                generic_subject,
                generic_account,
                tags=["generic"],
                source=DataSource.GENERIC,
            )
            await add_source_identity(session, generic_account, source=DataSource.GENERIC)
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=generic_subject,
                account=generic_account,
                source_acquired_at=NOW - timedelta(hours=2),
                committed_at=NOW - timedelta(hours=1),
                source_type=ImportSourceType.GENERIC_CSV,
            )
            await session.commit()

            records, total = await InfluencerRepository(session).list_influencers(
                InfluencerListQuery(), as_of=NOW, policy=FreshnessPolicy()
            )
            by_id = {record.influencer.id: record for record in records}

            assert total == 2
            mixed = freshness_by_account(by_id[mixed_subject.id])[mixed_account.id]
            assert mixed.last_observed_at == huitun_observed_at
            assert mixed.last_imported_at == huitun_observed_at + timedelta(hours=1)
            assert by_id[generic_subject.id].huitun_freshness == ()

    asyncio.run(scenario())


def test_huitun_state_or_identity_is_unknown_but_account_source_alone_is_ineligible() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            source_only = await add_influencer(session, name="source field only")
            await add_account(session, source_only, name="source-field-only-account")

            state_subject = await add_influencer(session, name="Huitun state")
            state_account = await add_account(session, state_subject, name="state-account")
            await add_source_state(session, state_subject, state_account, tags=["huitun"])

            identity_subject = await add_influencer(session, name="Huitun identity")
            identity_account = await add_account(session, identity_subject, name="identity-account")
            await add_source_identity(session, identity_account)
            await session.commit()
            repository = InfluencerRepository(session)

            records, total = await repository.list_influencers(
                InfluencerListQuery(), as_of=NOW, policy=FreshnessPolicy()
            )
            unknown, unknown_total = await repository.list_influencers(
                InfluencerListQuery(freshness_status=FreshnessStatus.UNKNOWN),
                as_of=NOW,
                policy=FreshnessPolicy(),
            )
            by_id = {record.influencer.id: record for record in records}

            assert total == 3
            assert by_id[source_only.id].huitun_freshness == ()
            for influencer_id, account_id in (
                (state_subject.id, state_account.id),
                (identity_subject.id, identity_account.id),
            ):
                freshness = freshness_by_account(by_id[influencer_id])[account_id]
                assert freshness.last_observed_at is None
                assert freshness.last_imported_at is None
            assert unknown_total == 2
            assert set(ids(unknown)) == {state_subject.id, identity_subject.id}

    asyncio.run(scenario())


def test_inactive_huitun_accounts_are_excluded_from_records_and_filters() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            operator, collection, stored_file = await add_import_context(session)
            mixed_subject = await add_influencer(session, name="active plus inactive")
            active = await add_account(session, mixed_subject, name="active-account")
            inactive = await add_account(
                session, mixed_subject, name="inactive-account", active=False
            )
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=mixed_subject,
                account=active,
                source_acquired_at=NOW - timedelta(days=1),
                committed_at=NOW - timedelta(hours=12),
            )
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=mixed_subject,
                account=inactive,
                source_acquired_at=NOW - timedelta(days=60),
                committed_at=NOW - timedelta(days=59),
            )

            inactive_only_subject = await add_influencer(session, name="inactive only")
            inactive_only = await add_account(
                session,
                inactive_only_subject,
                name="inactive-only-account",
                active=False,
            )
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=inactive_only_subject,
                account=inactive_only,
                source_acquired_at=NOW - timedelta(days=120),
                committed_at=NOW - timedelta(days=119),
            )
            await session.commit()
            repository = InfluencerRepository(session)

            records, total = await repository.list_influencers(
                InfluencerListQuery(), as_of=NOW, policy=FreshnessPolicy()
            )
            refresh_false, refresh_false_total = await repository.list_influencers(
                InfluencerListQuery(requires_refresh=False),
                as_of=NOW,
                policy=FreshnessPolicy(),
            )
            stale, stale_total = await repository.list_influencers(
                InfluencerListQuery(freshness_status=FreshnessStatus.STALE),
                as_of=NOW,
                policy=FreshnessPolicy(),
            )
            by_id = {record.influencer.id: record for record in records}

            assert total == refresh_false_total == 2
            assert set(ids(refresh_false)) == {mixed_subject.id, inactive_only_subject.id}
            assert set(freshness_by_account(by_id[mixed_subject.id])) == {active.id}
            assert by_id[inactive_only_subject.id].huitun_freshness == ()
            assert stale == [] and stale_total == 0

    asyncio.run(scenario())


def test_multi_account_freshness_keeps_every_account_and_any_required_refresh() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            operator, collection, stored_file = await add_import_context(session)

            mixed = await add_influencer(session, name="fresh and stale")
            mixed_fresh = await add_account(session, mixed, name="mixed-fresh")
            mixed_stale = await add_account(session, mixed, name="mixed-stale")
            for account, observed_at in (
                (mixed_fresh, NOW - timedelta(days=2)),
                (mixed_stale, NOW - timedelta(days=60)),
            ):
                await add_committed_lineage(
                    session,
                    operator=operator,
                    collection=collection,
                    stored_file=stored_file,
                    influencer=mixed,
                    account=account,
                    source_acquired_at=observed_at,
                    committed_at=observed_at + timedelta(hours=1),
                )

            all_fresh = await add_influencer(session, name="all fresh")
            fresh_accounts = [
                await add_account(session, all_fresh, name=f"fresh-{index}") for index in range(2)
            ]
            for index, account in enumerate(fresh_accounts, start=1):
                observed_at = NOW - timedelta(days=index)
                await add_committed_lineage(
                    session,
                    operator=operator,
                    collection=collection,
                    stored_file=stored_file,
                    influencer=all_fresh,
                    account=account,
                    source_acquired_at=observed_at,
                    committed_at=observed_at + timedelta(hours=1),
                )

            legacy = await add_influencer(session, name="legacy only")
            legacy_account = await add_account(session, legacy, name="legacy-account")
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=legacy,
                account=legacy_account,
                source_acquired_at=None,
                committed_at=NOW - timedelta(days=3),
                legacy_single_file=True,
            )
            await session.commit()
            repository = InfluencerRepository(session)

            records, total = await repository.list_influencers(
                InfluencerListQuery(), as_of=NOW, policy=FreshnessPolicy()
            )
            refresh_true, true_total = await repository.list_influencers(
                InfluencerListQuery(requires_refresh=True),
                as_of=NOW,
                policy=FreshnessPolicy(),
            )
            refresh_false, false_total = await repository.list_influencers(
                InfluencerListQuery(requires_refresh=False),
                as_of=NOW,
                policy=FreshnessPolicy(),
            )
            by_id = {record.influencer.id: record for record in records}

            assert total == 3
            assert set(freshness_by_account(by_id[mixed.id])) == {
                mixed_fresh.id,
                mixed_stale.id,
            }
            assert set(freshness_by_account(by_id[all_fresh.id])) == {
                account.id for account in fresh_accounts
            }
            assert set(freshness_by_account(by_id[legacy.id])) == {legacy_account.id}
            assert true_total == 2
            assert set(ids(refresh_true)) == {mixed.id, legacy.id}
            assert false_total == 1
            assert ids(refresh_false) == [all_fresh.id]

    asyncio.run(scenario())


def test_freshness_filters_are_exact_and_compose_with_all_existing_filters() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            operator, collection, stored_file = await add_import_context(session)
            fixtures: dict[str, Influencer] = {}

            unknown = await add_influencer(session, name="unknown evidence")
            unknown_account = await add_account(session, unknown, name="unknown-account")
            await add_source_state(session, unknown, unknown_account, tags=["unknown"])
            fixtures["unknown"] = unknown

            observed_by_name = {
                "fresh": NOW - timedelta(days=5),
                "aging": NOW - timedelta(days=20),
                "stale": NOW - timedelta(days=60),
                "very_stale": NOW - timedelta(days=120),
            }
            for name, observed_at in observed_by_name.items():
                is_filter_candidate = name in {"fresh", "stale"}
                influencer = await add_influencer(
                    session,
                    name=(f"Filtered Target {name}" if is_filter_candidate else f"{name} evidence"),
                    owner_id=operator.id if is_filter_candidate else None,
                    crm_stage=(
                        CRMStage.HIGH_INTENT if is_filter_candidate else CRMStage.TO_DEVELOP
                    ),
                )
                account = await add_account(
                    session,
                    influencer,
                    name=f"{name}-account",
                    tags=["Beauty"] if is_filter_candidate else [name],
                )
                row = await add_committed_lineage(
                    session,
                    operator=operator,
                    collection=collection,
                    stored_file=stored_file,
                    influencer=influencer,
                    account=account,
                    source_acquired_at=observed_at,
                    committed_at=observed_at + timedelta(hours=1),
                )
                await add_metrics(
                    session,
                    influencer,
                    account,
                    followers=300 if is_filter_candidate else 30,
                    import_job_id=row.import_job_id,
                    import_row_id=row.id,
                )
                fixtures[name] = influencer

            no_evidence = await add_influencer(session, name="no eligible Huitun evidence")
            await add_account(session, no_evidence, name="no-evidence-account")
            fixtures["no_evidence"] = no_evidence
            await session.commit()
            repository = InfluencerRepository(session)
            policy = FreshnessPolicy()

            expected_by_status = {
                FreshnessStatus.UNKNOWN: {fixtures["unknown"].id},
                FreshnessStatus.FRESH: {fixtures["fresh"].id},
                FreshnessStatus.AGING: {fixtures["aging"].id},
                FreshnessStatus.STALE: {fixtures["stale"].id},
                FreshnessStatus.VERY_STALE: {fixtures["very_stale"].id},
            }
            for status, expected in expected_by_status.items():
                records, total = await repository.list_influencers(
                    InfluencerListQuery(freshness_status=status),
                    as_of=NOW,
                    policy=policy,
                )
                assert total == len(expected)
                assert set(ids(records)) == expected

            refresh_true, refresh_true_total = await repository.list_influencers(
                InfluencerListQuery(requires_refresh=True),
                as_of=NOW,
                policy=policy,
            )
            refresh_false, refresh_false_total = await repository.list_influencers(
                InfluencerListQuery(requires_refresh=False),
                as_of=NOW,
                policy=policy,
            )
            assert refresh_true_total == 3
            assert set(ids(refresh_true)) == {
                fixtures["unknown"].id,
                fixtures["stale"].id,
                fixtures["very_stale"].id,
            }
            assert refresh_false_total == 3
            assert set(ids(refresh_false)) == {
                fixtures["fresh"].id,
                fixtures["aging"].id,
                fixtures["no_evidence"].id,
            }

            inclusive_boundary = NOW - timedelta(days=20)
            observed_before, before_total = await repository.list_influencers(
                InfluencerListQuery(last_huitun_observed_before=inclusive_boundary),
                as_of=NOW,
                policy=policy,
            )
            observed_after, after_total = await repository.list_influencers(
                InfluencerListQuery(last_huitun_observed_after=inclusive_boundary),
                as_of=NOW,
                policy=policy,
            )
            assert before_total == 3
            assert set(ids(observed_before)) == {
                fixtures["aging"].id,
                fixtures["stale"].id,
                fixtures["very_stale"].id,
            }
            assert after_total == 2
            assert set(ids(observed_after)) == {
                fixtures["fresh"].id,
                fixtures["aging"].id,
            }

            combined, combined_total = await repository.list_influencers(
                InfluencerListQuery(
                    q="Filtered Target",
                    tag="Beauty",
                    followers_min=250,
                    followers_max=350,
                    owner_operator_id=operator.id,
                    crm_stage=CRMStage.HIGH_INTENT,
                    freshness_status=FreshnessStatus.STALE,
                    requires_refresh=True,
                    last_huitun_observed_before=NOW - timedelta(days=45),
                    last_huitun_observed_after=NOW - timedelta(days=75),
                ),
                as_of=NOW,
                policy=policy,
            )
            assert combined_total == 1
            assert ids(combined) == [fixtures["stale"].id]

    asyncio.run(scenario())


def test_freshness_filtered_pagination_has_stable_created_at_id_order() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            operator, collection, stored_file = await add_import_context(session)
            shared_created_at = NOW - timedelta(hours=1)
            stale_ids = [UUID(int=index) for index in range(1, 6)]
            for influencer_id in stale_ids:
                influencer = await add_influencer(
                    session,
                    name=f"stale-{influencer_id.int}",
                    influencer_id=influencer_id,
                    created_at=shared_created_at,
                )
                account = await add_account(session, influencer, name=f"account-{influencer_id}")
                await add_committed_lineage(
                    session,
                    operator=operator,
                    collection=collection,
                    stored_file=stored_file,
                    influencer=influencer,
                    account=account,
                    source_acquired_at=NOW - timedelta(days=60),
                    committed_at=NOW - timedelta(days=59),
                )
            fresh = await add_influencer(session, name="newer but fresh", created_at=NOW)
            fresh_account = await add_account(session, fresh, name="fresh-account")
            await add_committed_lineage(
                session,
                operator=operator,
                collection=collection,
                stored_file=stored_file,
                influencer=fresh,
                account=fresh_account,
                source_acquired_at=NOW - timedelta(days=1),
                committed_at=NOW - timedelta(hours=12),
            )
            await session.commit()
            repository = InfluencerRepository(session)

            pages: list[list[UUID]] = []
            totals: list[int] = []
            for page in range(1, 4):
                records, total = await repository.list_influencers(
                    InfluencerListQuery(
                        freshness_status=FreshnessStatus.STALE,
                        page=page,
                        page_size=2,
                    ),
                    as_of=NOW,
                    policy=FreshnessPolicy(),
                )
                pages.append(ids(records))
                totals.append(total)

            assert totals == [5, 5, 5]
            assert pages == [
                [UUID(int=5), UUID(int=4)],
                [UUID(int=3), UUID(int=2)],
                [UUID(int=1)],
            ]

    asyncio.run(scenario())


def test_combined_contact_and_notes_filters_have_fixed_query_count() -> None:
    async def scenario() -> None:
        statements: list[str] = []
        async with database_session(statements=statements) as session:
            operator, collection, stored_file = await add_import_context(session)
            for index in range(1, 51):
                influencer = await add_influencer(
                    session,
                    name=f"query-count-{index:02d}",
                    influencer_id=UUID(int=1000 + index),
                )
                account = await add_account(session, influencer, name=f"account-{index:02d}")
                row = await add_committed_lineage(
                    session,
                    operator=operator,
                    collection=collection,
                    stored_file=stored_file,
                    influencer=influencer,
                    account=account,
                    source_acquired_at=NOW - timedelta(days=1),
                    committed_at=NOW - timedelta(hours=12),
                )
                await add_metrics(
                    session,
                    influencer,
                    account,
                    followers=index,
                    notes_7d=0,
                    notes_60d=0,
                    import_job_id=row.import_job_id,
                    import_row_id=row.id,
                )
                await add_contact(
                    session,
                    influencer,
                    value=f"{index}@example.test",
                    account=account,
                )
            await session.commit()
            statements.clear()

            records, total = await InfluencerRepository(session).list_influencers(
                InfluencerListQuery(
                    contact_filter=ContactFilter.HAS_EMAIL,
                    notes_7d_filter=Notes7dFilter.ZERO,
                    notes_60d_filter=Notes60dFilter.ZERO,
                    page_size=50,
                ),
                as_of=NOW,
                policy=FreshnessPolicy(),
            )

            assert total == len(records) == 50
            assert all(len(record.huitun_freshness) == 1 for record in records)
            assert len(statements) == 6

    asyncio.run(scenario())


def test_contact_and_activity_filters_use_current_active_data_with_stable_pages() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            fixtures: dict[str, Influencer] = {}
            for index, (name, notes_7d, notes_60d) in enumerate(
                (
                    ("zero", 0, 0),
                    ("one", 1, 1),
                    ("two", 2, 2),
                    ("three", 3, 3),
                    ("nine", 9, 9),
                    ("ten", 10, 10),
                    ("missing", None, None),
                ),
                start=1,
            ):
                influencer = await add_influencer(
                    session,
                    name=name,
                    influencer_id=UUID(int=index),
                    created_at=NOW,
                )
                account = await add_account(
                    session,
                    influencer,
                    name=f"{name}-account",
                    tags=["科技3C"] if name == "zero" else None,
                )
                await add_metrics(
                    session,
                    influencer,
                    account,
                    followers=100_000 if name == "zero" else 10,
                    notes_7d=notes_7d,
                    notes_60d=notes_60d,
                )
                fixtures[name] = influencer

            await add_contact(
                session,
                fixtures["zero"],
                value="zero@example.test",
            )
            await add_contact(
                session,
                fixtures["one"],
                value="one@example.test",
                contact_type=ContactType.PHONE,
            )
            await add_contact(
                session,
                fixtures["two"],
                value="former@example.test",
                current=False,
            )
            inactive_account = await add_account(
                session,
                fixtures["missing"],
                name="inactive-notes",
                active=False,
            )
            await add_metrics(
                session,
                fixtures["missing"],
                inactive_account,
                followers=100,
                notes_7d=10,
                notes_60d=10,
            )

            repository = InfluencerRepository(session)

            async def names(query: InfluencerListQuery) -> tuple[set[str], int]:
                records, total = await repository.list_influencers(query)
                return {record.influencer.display_name for record in records}, total

            for filter_value, expected in (
                (Notes60dFilter.ZERO, {"zero"}),
                (Notes60dFilter.ONE_TO_TWO, {"one", "two"}),
                (Notes60dFilter.THREE_TO_NINE, {"three", "nine"}),
                (Notes60dFilter.TEN_OR_MORE, {"ten"}),
                (Notes60dFilter.MISSING, {"missing"}),
            ):
                filtered, total = await names(InfluencerListQuery(notes_60d_filter=filter_value))
                assert filtered == expected
                assert total == len(expected)

            for filter_value, expected in (
                (Notes7dFilter.ZERO, {"zero"}),
                (Notes7dFilter.ONE_TO_TWO, {"one", "two"}),
                (Notes7dFilter.THREE_PLUS, {"three", "nine", "ten"}),
                (Notes7dFilter.MISSING, {"missing"}),
            ):
                filtered, total = await names(InfluencerListQuery(notes_7d_filter=filter_value))
                assert filtered == expected
                assert total == len(expected)

            has_contact, has_contact_total = await names(
                InfluencerListQuery(contact_filter=ContactFilter.HAS_CONTACT)
            )
            has_email, has_email_total = await names(
                InfluencerListQuery(contact_filter=ContactFilter.HAS_EMAIL)
            )
            no_contact, no_contact_total = await names(
                InfluencerListQuery(contact_filter=ContactFilter.NO_CONTACT)
            )
            assert has_contact == {"zero", "one"}
            assert has_contact_total == 2
            assert has_email == {"zero"}
            assert has_email_total == 1
            assert no_contact == {"two", "three", "nine", "ten", "missing"}
            assert no_contact_total == 5

            combined, total = await names(
                InfluencerListQuery(
                    tag="科技3C",
                    followers_min=100_000,
                    followers_max=100_000,
                    contact_filter=ContactFilter.HAS_EMAIL,
                    notes_7d_filter=Notes7dFilter.ZERO,
                    notes_60d_filter=Notes60dFilter.ZERO,
                )
            )
            assert combined == {"zero"}
            assert total == 1

            first_page, first_total = await repository.list_influencers(
                InfluencerListQuery(contact_filter=ContactFilter.NO_CONTACT, page=1, page_size=2)
            )
            second_page, second_total = await repository.list_influencers(
                InfluencerListQuery(contact_filter=ContactFilter.NO_CONTACT, page=2, page_size=2)
            )
            assert first_total == second_total == 5
            assert set(ids(first_page)).isdisjoint(ids(second_page))

    asyncio.run(scenario())


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
