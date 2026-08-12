"""Typed, chunked repository primitives for Phase 2 bulk planning."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.bulk_repository import (
    AccountHandleKey,
    AccountSourceKey,
    BulkImportContext,
    BulkImportRepository,
    ContactValueKey,
    ExternalIdentityKey,
    PlatformAccountIdKey,
    PrefetchCoverageError,
    PrefetchedImportRepository,
    ProfileUrlKey,
    SourceContactKey,
    SourceIdentityKey,
    advisory_lock_keys,
    iter_safe_chunks,
)
from backend_core.imports.contracts import (
    CanonicalContact,
    CanonicalInfluencerRecord,
    PlatformIdentity,
)
from backend_core.imports.enums import ImportMatchType
from backend_core.imports.hashing import hash_document
from backend_core.imports.planner import ImportPlanner, metric_snapshot_key
from backend_core.imports.repository import ImportRepository
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    DataSource,
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
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _record(
    *,
    account_id: str = "account-a",
    external_id: str = "external-a",
    handle: str = "handle-a",
    normalized_profile_url: str = "https://www.xiaohongshu.com/user/profile/account-a",
    email: str = "shared@example.invalid",
) -> CanonicalInfluencerRecord:
    return CanonicalInfluencerRecord(
        display_name="Fixture",
        platform_identity=PlatformIdentity(
            platform=Platform.XIAOHONGSHU,
            platform_account_id=account_id,
            account_handle=handle,
            profile_url=normalized_profile_url,
            normalized_profile_url=normalized_profile_url,
            external_source_id=external_id,
        ),
        source=DataSource.GENERIC,
        source_updated_at=datetime(2026, 8, 12, tzinfo=UTC),
        contacts=(
            CanonicalContact(
                type=ContactType.EMAIL,
                value=email,
                normalized_value=email,
                validation_status=ContactValidationStatus.VALID,
            ),
        ),
    )


def test_safe_chunks_are_sorted_deduplicated_and_bounded() -> None:
    assert list(iter_safe_chunks(["b", "a", "b", "d", "c"], chunk_size=2)) == [
        ("a", "b"),
        ("c", "d"),
    ]
    assert list(iter_safe_chunks([], chunk_size=2)) == []
    with pytest.raises(ValueError, match="chunk_size"):
        list(iter_safe_chunks(["a"], chunk_size=0))


def test_context_extracts_typed_query_keys_without_promoting_soft_identity() -> None:
    record = _record()
    invalid_contact_record = record.model_copy(
        update={
            "contacts": (
                CanonicalContact(
                    type=ContactType.EMAIL,
                    value="invalid@example.invalid",
                    normalized_value="invalid@example.invalid",
                    validation_status=ContactValidationStatus.INVALID,
                ),
            )
        }
    )
    context = BulkImportContext.from_records(
        [record, record, invalid_contact_record],
        snapshot_keys=["snapshot-b", "snapshot-a", "snapshot-b"],
    )

    platform_key = PlatformAccountIdKey(Platform.XIAOHONGSHU, "account-a")
    profile_key = ProfileUrlKey(
        Platform.XIAOHONGSHU,
        "https://www.xiaohongshu.com/user/profile/account-a",
    )
    handle_key = AccountHandleKey(Platform.XIAOHONGSHU, "handle-a")
    external_key = ExternalIdentityKey(
        DataSource.GENERIC,
        Platform.XIAOHONGSHU,
        "external-a",
    )
    contact_key = ContactValueKey(ContactType.EMAIL, "shared@example.invalid")

    assert context.platform_account_ids == frozenset({platform_key})
    assert context.normalized_profile_urls == frozenset({profile_key})
    assert context.external_source_ids == frozenset({external_key})
    assert context.account_handles == frozenset({handle_key})
    assert context.contact_values == frozenset({contact_key})
    assert ContactValueKey(ContactType.EMAIL, "invalid@example.invalid") not in (
        context.contact_values
    )
    assert context.sources == frozenset({DataSource.GENERIC})
    assert context.snapshot_keys == frozenset({"snapshot-a", "snapshot-b"})
    # Handle and Email are deliberately separate lookup sets; neither is a
    # frozen hard identity key.
    assert handle_key not in context.platform_account_ids
    assert all("shared@example.invalid" not in key.value for key in context.hard_identity_keys)

    same_value = BulkImportContext.from_records(
        [_record(account_id="same-value", handle="same-value")]
    )
    assert PlatformAccountIdKey(Platform.XIAOHONGSHU, "same-value") in (
        same_value.hard_identity_keys
    )
    assert AccountHandleKey(Platform.XIAOHONGSHU, "same-value") not in (
        same_value.hard_identity_keys
    )


def test_advisory_lock_keys_are_stable_unique_and_ordered() -> None:
    first = advisory_lock_keys(["identity-b", "identity-a", "identity-b"])
    second = advisory_lock_keys(["identity-a", "identity-b"])
    assert first == second
    assert first == tuple(sorted(set(first)))


@asynccontextmanager
async def _database() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], list[str]]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    statements: list[str] = []

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    statements.clear()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory, statements
    finally:
        await engine.dispose()


async def _seed_state(session: AsyncSession) -> tuple[
    Influencer,
    Influencer,
    InfluencerPlatformAccount,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    InfluencerCurrentMetrics,
    PlatformAccountSourceIdentity,
    InfluencerContact,
    InfluencerContact,
]:
    influencer_a = Influencer(id=UUID(int=11), display_name="Influencer A")
    influencer_b = Influencer(id=UUID(int=12), display_name="Influencer B")
    session.add_all((influencer_a, influencer_b))
    await session.flush()
    account_a = InfluencerPlatformAccount(
        id=UUID(int=21),
        influencer_id=influencer_a.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id="account-a",
        account_name="Account A",
        account_handle="handle-a",
        profile_url="https://www.xiaohongshu.com/user/profile/account-a",
        normalized_profile_url="https://www.xiaohongshu.com/user/profile/account-a",
        source=DataSource.GENERIC,
        is_active=True,
    )
    # Handle is intentionally non-unique and remains only potential-review
    # evidence.  Deterministic tuple ordering must not rely on query order.
    account_b = InfluencerPlatformAccount(
        id=UUID(int=22),
        influencer_id=influencer_b.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id="account-b",
        account_name="Account B",
        account_handle="handle-a",
        source=DataSource.GENERIC,
        is_active=True,
    )
    session.add_all((account_b, account_a))
    await session.flush()
    source_identity = PlatformAccountSourceIdentity(
        id=UUID(int=31),
        platform_account_id=account_a.id,
        platform=Platform.XIAOHONGSHU,
        source=DataSource.GENERIC,
        external_account_id="external-a",
        first_import_job_id=UUID(int=101),
        first_import_row_id=UUID(int=102),
        last_import_job_id=UUID(int=101),
        last_import_row_id=UUID(int=102),
    )
    source_state = InfluencerSourceState(
        id=UUID(int=32),
        influencer_id=influencer_a.id,
        platform_account_id=account_a.id,
        source=DataSource.GENERIC,
        source_data={"display_name": "Existing"},
        source_data_hash="a" * 64,
        state_version=1,
        last_import_job_id=UUID(int=101),
        last_import_row_id=UUID(int=102),
    )
    current_metrics = InfluencerCurrentMetrics(
        id=UUID(int=33),
        influencer_id=influencer_a.id,
        platform_account_id=account_a.id,
        source=DataSource.GENERIC,
        metrics={"followers_count": 10},
        metrics_hash="b" * 64,
        last_import_job_id=UUID(int=101),
        last_import_row_id=UUID(int=102),
    )
    source_contact = InfluencerContact(
        id=UUID(int=41),
        influencer_id=influencer_a.id,
        platform_account_id=account_a.id,
        type=ContactType.EMAIL,
        value="shared@example.invalid",
        normalized_value="shared@example.invalid",
        source=DataSource.GENERIC,
        validation_status=ContactValidationStatus.VALID,
        is_current=True,
        possible_duplicate_contact=False,
        first_seen_at=datetime(2026, 8, 1, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 1, tzinfo=UTC),
        first_import_job_id=UUID(int=101),
        first_import_row_id=UUID(int=102),
        last_import_job_id=UUID(int=101),
        last_import_row_id=UUID(int=102),
    )
    duplicate_contact = InfluencerContact(
        id=UUID(int=42),
        influencer_id=influencer_b.id,
        platform_account_id=None,
        type=ContactType.EMAIL,
        value="shared@example.invalid",
        normalized_value="shared@example.invalid",
        source=DataSource.MANUAL,
        validation_status=ContactValidationStatus.VALID,
        is_current=True,
        possible_duplicate_contact=False,
        first_seen_at=datetime(2026, 8, 1, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add_all(
        (
            source_identity,
            source_state,
            current_metrics,
            source_contact,
            duplicate_contact,
            InfluencerMetricSnapshot(
                id=UUID(int=51),
                influencer_id=influencer_a.id,
                platform_account_id=account_a.id,
                source=DataSource.GENERIC,
                source_updated_at=datetime(2026, 8, 1, tzinfo=UTC),
                import_job_id=UUID(int=101),
                import_row_id=UUID(int=103),
                captured_at=datetime(2026, 8, 1, tzinfo=UTC),
                metrics={"followers_count": 10},
                metrics_hash="b" * 64,
                snapshot_key="snapshot-existing",
            ),
        )
    )
    await session.commit()
    return (
        influencer_a,
        influencer_b,
        account_a,
        account_b,
        source_state,
        current_metrics,
        source_identity,
        source_contact,
        duplicate_contact,
    )


def test_prefetch_returns_complete_typed_deterministic_state() -> None:
    async def scenario() -> None:
        async with _database() as (factory, _statements):
            async with factory() as session:
                (
                    influencer_a,
                    influencer_b,
                    account_a,
                    account_b,
                    source_state,
                    current_metrics,
                    source_identity,
                    source_contact,
                    duplicate_contact,
                ) = await _seed_state(session)
                context = BulkImportContext.from_records(
                    [_record()],
                    snapshot_keys=["snapshot-existing", "snapshot-new"],
                )

                state = await BulkImportRepository(session).prefetch(context)

                platform_key = PlatformAccountIdKey(Platform.XIAOHONGSHU, "account-a")
                profile_key = ProfileUrlKey(
                    Platform.XIAOHONGSHU,
                    "https://www.xiaohongshu.com/user/profile/account-a",
                )
                handle_key = AccountHandleKey(Platform.XIAOHONGSHU, "handle-a")
                external_key = ExternalIdentityKey(
                    DataSource.GENERIC,
                    Platform.XIAOHONGSHU,
                    "external-a",
                )
                assert state.accounts_by_platform_id[platform_key] == (account_a,)
                assert state.accounts_by_profile_url[profile_key] == (account_a,)
                assert state.accounts_by_external_id[external_key] == (account_a,)
                assert state.accounts_by_handle[handle_key] == (account_a, account_b)
                # Handle-only candidates stay available to Matcher as potential
                # review evidence but do not expand dependent merge-state reads.
                assert state.accounts_by_id == {account_a.id: account_a}
                assert state.influencers_by_id == {influencer_a.id: influencer_a}
                assert (
                    state.source_identities[
                        SourceIdentityKey(account_a.id, DataSource.GENERIC, "external-a")
                    ]
                    is source_identity
                )
                assert (
                    state.source_states[AccountSourceKey(account_a.id, DataSource.GENERIC)]
                    is source_state
                )
                assert (
                    state.current_metrics[AccountSourceKey(account_a.id, DataSource.GENERIC)]
                    is current_metrics
                )
                assert state.existing_snapshot_keys == frozenset({"snapshot-existing"})
                assert state.source_contacts[
                    SourceContactKey(
                        influencer_a.id,
                        DataSource.GENERIC,
                        ContactType.EMAIL,
                    )
                ] == (source_contact,)
                assert state.contacts_by_normalized_value[
                    ContactValueKey(ContactType.EMAIL, "shared@example.invalid")
                ] == (source_contact, duplicate_contact)

    asyncio.run(scenario())


def test_prefetch_query_count_depends_on_unique_chunks_not_record_count() -> None:
    async def scenario() -> None:
        async with _database() as (factory, statements):
            async with factory() as session:
                await _seed_state(session)
                repository = BulkImportRepository(session)
                single = BulkImportContext.from_records([_record()])
                repeated = BulkImportContext.from_records([_record()] * 2_000)

                statements.clear()
                empty_state = await repository.prefetch(BulkImportContext.from_records([]))
                assert statements == []
                assert empty_state.accounts_by_id == {}

                statements.clear()
                await repository.prefetch(single)
                single_selects = sum(
                    statement.lstrip().upper().startswith("SELECT") for statement in statements
                )
                statements.clear()
                await repository.prefetch(repeated)
                repeated_selects = sum(
                    statement.lstrip().upper().startswith("SELECT") for statement in statements
                )

                assert single_selects > 0
                assert repeated_selects == single_selects

                statements.clear()
                acquired = await repository.acquire_identity_locks_bulk(
                    ["identity-b", "identity-a", "identity-b"]
                )
                assert acquired == advisory_lock_keys(["identity-a", "identity-b"])
                assert statements == []  # SQLite/non-PostgreSQL is a strict no-op.

    asyncio.run(scenario())


def test_prefetched_adapter_is_strict_and_matcher_results_are_differentially_equal() -> None:
    async def scenario() -> None:
        async with _database() as (factory, _statements):
            async with factory() as session:
                await _seed_state(session)
                records = (
                    _record(),
                    # Two hard keys resolve to different accounts.
                    _record(
                        account_id="account-a",
                        external_id="missing-external",
                        normalized_profile_url=(
                            "https://www.xiaohongshu.com/user/profile/account-b"
                        ),
                    ),
                    # No hard match, but the existing handle is potential-review
                    # evidence only.
                    _record(
                        account_id="missing-account",
                        external_id="missing-external",
                        normalized_profile_url=(
                            "https://www.xiaohongshu.com/user/profile/missing-account"
                        ),
                    ),
                )
                account_b = await session.get(InfluencerPlatformAccount, UUID(int=22))
                assert account_b is not None
                account_b.profile_url = "https://www.xiaohongshu.com/user/profile/account-b"
                account_b.normalized_profile_url = account_b.profile_url
                await session.commit()
                context = BulkImportContext.from_records(records)
                state = await BulkImportRepository(session).prefetch(context)
                prefetched = PrefetchedImportRepository(state)
                database_planner = ImportPlanner(ImportRepository(session))
                prefetched_planner = ImportPlanner(prefetched)

                prefetched_results = []
                for record in records:
                    prefetched_result = await prefetched_planner.match(record)
                    prefetched_results.append(prefetched_result)
                    assert prefetched_result == await database_planner.match(record)
                assert prefetched_results[0].match_type is ImportMatchType.PLATFORM_ACCOUNT_ID
                assert prefetched_results[0].manual_review is False
                assert prefetched_results[1].manual_review is True
                assert {warning.code for warning in prefetched_results[1].warnings} == {
                    "IDENTITY_CONFLICT"
                }
                assert prefetched_results[2].manual_review is True
                assert {warning.code for warning in prefetched_results[2].warnings} == {
                    "ACCOUNT_HANDLE_POTENTIAL_MATCH"
                }

                with pytest.raises(PrefetchCoverageError, match="PlatformAccountIdKey") as error:
                    await prefetched.accounts_by_platform_id(
                        Platform.XIAOHONGSHU, "outside-context"
                    )
                assert "outside-context" not in str(error.value)

                with pytest.raises(PrefetchCoverageError, match="ContactValueKey") as error:
                    await prefetched.contacts_with_normalized_value(
                        ContactType.EMAIL, "private-contact@example.invalid"
                    )
                assert "private-contact@example.invalid" not in str(error.value)

    asyncio.run(scenario())


def test_prefetched_planner_is_exact_for_state_metrics_snapshot_and_contacts() -> None:
    async def scenario() -> None:
        async with _database() as (factory, _statements):
            async with factory() as session:
                await _seed_state(session)
                record = _record().model_copy(
                    update={
                        "source_updated_at": datetime(2026, 8, 1, tzinfo=UTC),
                        "metrics": {"followers_count": 10},
                    }
                )
                snapshot_key = metric_snapshot_key(record)
                snapshot = await session.get(InfluencerMetricSnapshot, UUID(int=51))
                assert snapshot is not None
                snapshot.snapshot_key = snapshot_key
                snapshot.metrics_hash = hash_document(record.metrics)
                await session.commit()

                context = BulkImportContext.from_records([record], snapshot_keys=[snapshot_key])
                state = await BulkImportRepository(session).prefetch(context)
                prefetched = PrefetchedImportRepository(state)
                job_id = UUID(int=501)
                arguments = {
                    "job_id": job_id,
                    "row_number": 2,
                    "record": record,
                    "normalized_data": record.as_dict(),
                    "mapping_hash": "f" * 64,
                    "preview_revision": 0,
                    "row_locator": {
                        "import_job_file_id": str(UUID(int=502)),
                        "row_number": 2,
                    },
                }

                database_plan = await ImportPlanner(ImportRepository(session)).plan(**arguments)
                prefetched_plan = await ImportPlanner(prefetched).plan(**arguments)

                assert prefetched_plan == database_plan
                assert prefetched_plan.plan_hash == database_plan.plan_hash
                assert prefetched_plan.merge_plan["metrics"]["snapshot"] is None
                assert prefetched_plan.merge_plan["contacts"]["create"] == []
                assert prefetched_plan.merge_plan["contacts"]["observe_ids"] == [str(UUID(int=41))]
                assert prefetched_plan.merge_plan["contacts"]["mark_duplicate_ids"] == [
                    str(UUID(int=41))
                ]

    asyncio.run(scenario())


def test_reference_and_prefetched_planners_are_exact_for_fifty_mixed_rows() -> None:
    async def scenario() -> None:
        async with _database() as (factory, _statements):
            async with factory() as session:
                await _seed_state(session)
                existing = _record().model_copy(
                    update={
                        "source_updated_at": datetime(2026, 8, 1, tzinfo=UTC),
                        "metrics": {"followers_count": 10},
                    }
                )
                new = _record(
                    account_id="new-account",
                    external_id="new-external",
                    handle="new-handle",
                    normalized_profile_url=("https://www.xiaohongshu.com/user/profile/new-account"),
                    email="new@example.invalid",
                )
                potential_handle = _record(
                    account_id="missing-account",
                    external_id="missing-external",
                    handle="handle-a",
                    normalized_profile_url=(
                        "https://www.xiaohongshu.com/user/profile/missing-account"
                    ),
                    email="shared@example.invalid",
                )
                records = tuple((existing, new, potential_handle)[index % 3] for index in range(50))
                snapshot_keys = tuple(metric_snapshot_key(record) for record in records)
                state = await BulkImportRepository(session).prefetch(
                    BulkImportContext.from_records(records, snapshot_keys=snapshot_keys)
                )
                reference_planner = ImportPlanner(ImportRepository(session))
                prefetched_planner = ImportPlanner(PrefetchedImportRepository(state))

                for index, record in enumerate(records, start=2):
                    assert await prefetched_planner.match(record) == await reference_planner.match(
                        record
                    )
                    arguments = {
                        "job_id": UUID(int=601),
                        "row_number": index,
                        "record": record,
                        "normalized_data": record.as_dict(),
                        "mapping_hash": "e" * 64,
                        "preview_revision": 0,
                        "row_locator": {
                            "import_job_file_id": str(UUID(int=602)),
                            "row_number": index,
                        },
                    }
                    reference_plan = await reference_planner.plan(**arguments)
                    prefetched_plan = await prefetched_planner.plan(**arguments)
                    assert prefetched_plan == reference_plan
                    assert prefetched_plan.plan_hash == reference_plan.plan_hash

    asyncio.run(scenario())
