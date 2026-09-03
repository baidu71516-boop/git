"""Focused production-contract tests for Huitun historical reprojection."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, Operator
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.bulk_repository import AccountSourceKey
from backend_core.imports.contracts import CanonicalInfluencerRecord, PlatformIdentity
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
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.hashing import hash_document
from backend_core.imports.huitun_reprojection import HuitunReprojectionService
from backend_core.imports.huitun_reprojection_cli import main, parse_args
from backend_core.imports.merge_applier import ImportMergeApplier, MergeApplyCache
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportRow,
    StoredImportFile,
)
from backend_core.imports.planner import (
    ImportPlanner,
    PlannedImportRow,
    metric_snapshot_projection,
)
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
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

COMMITTED_AT = datetime(2026, 8, 14, 8, 30, tzinfo=UTC)
RAW_DATA = {
    "播主昵称": "舞蹈达人",
    "抖音号": "dance-creator",
    "达人主页链接": "https://www.douyin.com/user/dance-token",
    "分类": "舞蹈",
    "粉丝数": "871798.0",
}


@dataclass(frozen=True, slots=True)
class ReprojectionFixture:
    department_id: UUID
    import_job_id: UUID
    row_id: UUID
    collection_id: UUID
    influencer_id: UUID
    account_id: UUID
    contact_id: UUID


@asynccontextmanager
async def database_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def seed_reprojection_fixture(
    session: AsyncSession,
    *,
    source_type: ImportSourceType = ImportSourceType.MANUAL_HUITUN_EXPORT,
    job_status: ImportJobStatus = ImportJobStatus.COMPLETED,
    followers: str = "871798.0",
) -> ReprojectionFixture:
    department = Department(
        name=f"Reprojection department {uuid4().hex}",
        password_hash="not-used-by-test",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name="Reprojection operator",
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    session.add(operator)
    await session.flush()
    collection = CollectionJob(
        name="Reprojection collection",
        industry="影视",
        subdirection="剧评",
        purpose="Historical Huitun repair",
        target_action="repair",
        target_count=1,
        department_id=department.id,
        owner_operator_id=operator.id,
        source_type=source_type,
        status=CollectionJobStatus.COMPLETED,
    )
    stored_file = StoredImportFile(
        sha256=uuid4().hex + uuid4().hex,
        storage_key=f"reprojection/{uuid4().hex}.xlsx",
        size=1,
        detected_type=StoredFileType.XLSX,
        detected_mime=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        expires_at=COMMITTED_AT,
    )
    session.add_all((collection, stored_file))
    await session.flush()
    job = ImportJob(
        collection_job_id=collection.id,
        department_id=department.id,
        operator_id=operator.id,
        stored_file_id=stored_file.id,
        original_filename="huitun-historical.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_size=1,
        sha256=stored_file.sha256,
        source_type=source_type,
        status=job_status,
        preview_revision=3,
        confirmed_revision=3,
        confirmed_at=COMMITTED_AT,
        completed_at=COMMITTED_AT,
    )
    session.add(job)
    await session.flush()
    import_file = ImportJobFile(
        import_job_id=job.id,
        stored_file_id=stored_file.id,
        position=1,
        original_filename="huitun-historical.xlsx",
        declared_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        status=ImportJobFileStatus.READY,
        source_acquired_at=None,
        source_acquired_at_origin=SourceAcquiredAtOrigin.LEGACY_UNKNOWN,
    )
    influencer = Influencer(id=uuid4(), display_name="原始达人")
    account = InfluencerPlatformAccount(
        id=uuid4(),
        influencer_id=influencer.id,
        platform=Platform.DOUYIN,
        platform_account_id="dance-token",
        account_name="舞蹈达人",
        account_handle="dance-creator",
        profile_url="https://www.douyin.com/user/dance-token",
        normalized_profile_url="https://www.douyin.com/user/dance-token",
        source=DataSource.HUITUN,
        is_active=True,
    )
    session.add_all((import_file, influencer, account))
    await session.flush()
    raw_data = {**RAW_DATA, "粉丝数": followers}
    row = ImportRow(
        id=uuid4(),
        import_job_id=job.id,
        import_job_file_id=import_file.id,
        row_number=2,
        raw_data=raw_data,
        normalized_data={"historical": "bad-projection-is-not-input"},
        matched_influencer_id=influencer.id,
        matched_platform_account_id=account.id,
        match_type=ImportMatchType.PLATFORM_ACCOUNT_ID,
        action=ImportRowAction.CREATE,
        merge_plan={},
        warnings=[],
        errors=[],
        preview_revision=3,
        plan_hash="a" * 64,
        committed_action=ImportRowAction.CREATE,
        committed_at=COMMITTED_AT,
    )
    session.add(row)
    await session.flush()
    original_source_data = {
        "display_name": "舞蹈达人",
        "account_handle": "dance-creator",
        "profile_url": "https://www.douyin.com/user/dance-token",
        "normalized_profile_url": "https://www.douyin.com/user/dance-token",
    }
    source_state = InfluencerSourceState(
        influencer_id=influencer.id,
        platform_account_id=account.id,
        source=DataSource.HUITUN,
        source_updated_at=None,
        source_data=original_source_data,
        source_data_hash=hash_document(original_source_data),
        state_version=1,
        last_import_job_id=job.id,
        last_import_row_id=row.id,
    )
    contact = InfluencerContact(
        influencer_id=influencer.id,
        platform_account_id=account.id,
        type=ContactType.EMAIL,
        value="historical@example.test",
        normalized_value="historical@example.test",
        source=DataSource.HUITUN,
        validation_status=ContactValidationStatus.VALID,
        is_current=True,
        possible_duplicate_contact=False,
        first_seen_at=COMMITTED_AT,
        last_seen_at=COMMITTED_AT,
        source_updated_at=None,
        first_import_job_id=job.id,
        first_import_row_id=row.id,
        last_import_job_id=job.id,
        last_import_row_id=row.id,
    )
    session.add_all((source_state, contact))
    await session.commit()
    return ReprojectionFixture(
        department_id=department.id,
        import_job_id=job.id,
        row_id=row.id,
        collection_id=collection.id,
        influencer_id=influencer.id,
        account_id=account.id,
        contact_id=contact.id,
    )


async def count(session: AsyncSession, model: type[object]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def source_state_for(
    session: AsyncSession, fixture: ReprojectionFixture
) -> InfluencerSourceState:
    state = await session.scalar(
        select(InfluencerSourceState).where(
            InfluencerSourceState.platform_account_id == fixture.account_id,
            InfluencerSourceState.source == DataSource.HUITUN,
        )
    )
    assert state is not None
    return state


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def test_reproject_exact_confirmed_huitun_job_restores_only_allowed_projections() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            fixture = await seed_reprojection_fixture(session)
            raw_before = dict((await session.get(ImportRow, fixture.row_id)).raw_data)  # type: ignore[union-attr]
            collection = await session.get(CollectionJob, fixture.collection_id)
            contact = await session.get(InfluencerContact, fixture.contact_id)
            assert collection is not None and contact is not None
            collection_before = (collection.industry, collection.subdirection)
            contact_before = (
                contact.value,
                contact.is_current,
                contact.last_seen_at,
                contact.last_import_job_id,
                contact.last_import_row_id,
            )

            result = await HuitunReprojectionService(session).reproject(
                department_id=fixture.department_id,
                import_job_id=fixture.import_job_id,
            )

            row = await session.get(ImportRow, fixture.row_id)
            account = await session.get(InfluencerPlatformAccount, fixture.account_id)
            influencer = await session.get(Influencer, fixture.influencer_id)
            collection = await session.get(CollectionJob, fixture.collection_id)
            contact = await session.get(InfluencerContact, fixture.contact_id)
            state = await source_state_for(session, fixture)
            current = await session.scalar(
                select(InfluencerCurrentMetrics).where(
                    InfluencerCurrentMetrics.platform_account_id == fixture.account_id,
                    InfluencerCurrentMetrics.source == DataSource.HUITUN,
                )
            )
            snapshot = await session.scalar(
                select(InfluencerMetricSnapshot).where(
                    InfluencerMetricSnapshot.import_row_id == fixture.row_id
                )
            )
            assert row is not None and account is not None and influencer is not None
            assert collection is not None and contact is not None
            assert current is not None and snapshot is not None
            assert result == {
                "department_id": str(fixture.department_id),
                "import_job_id": str(fixture.import_job_id),
                "selected_committed_rows": 1,
                "processed": 1,
                "source_states_repaired": 1,
                "source_states_noop": 0,
                "metric_snapshots_inserted": 1,
                "metric_snapshots_noop": 0,
                "conflicts": 0,
                "errors": 0,
            }
            assert state.source_data["creator_classification_tags"] == ["舞蹈"]
            assert current.metrics["followers_count"] == 871798
            assert snapshot.metrics == {"followers_count": 871798}
            assert snapshot.metrics_hash == hash_document(snapshot.metrics)
            assert snapshot.import_job_id == fixture.import_job_id
            assert snapshot.import_row_id == fixture.row_id
            assert snapshot.platform_account_id == fixture.account_id
            assert snapshot.influencer_id == fixture.influencer_id
            assert snapshot.source is DataSource.HUITUN
            assert as_utc(snapshot.captured_at) == COMMITTED_AT
            assert row.raw_data == raw_before
            assert row.matched_influencer_id == fixture.influencer_id
            assert row.matched_platform_account_id == fixture.account_id
            assert account.id == fixture.account_id
            assert account.influencer_id == fixture.influencer_id
            assert influencer.id == fixture.influencer_id
            assert (collection.industry, collection.subdirection) == collection_before
            assert (
                contact.value,
                contact.is_current,
                contact.last_seen_at,
                contact.last_import_job_id,
                contact.last_import_row_id,
            ) == contact_before
            assert await count(session, Influencer) == 1
            assert await count(session, InfluencerPlatformAccount) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("wrong_department", "source_type", "job_status", "expected_code"),
    [
        (
            True,
            ImportSourceType.MANUAL_HUITUN_EXPORT,
            ImportJobStatus.COMPLETED,
            "IMPORT_JOB_NOT_FOUND",
        ),
        (
            False,
            ImportSourceType.GENERIC_CSV,
            ImportJobStatus.COMPLETED,
            "HUITUN_REPROJECTION_SOURCE_INVALID",
        ),
        (
            False,
            ImportSourceType.MANUAL_HUITUN_EXPORT,
            ImportJobStatus.PREVIEW_READY,
            "HUITUN_REPROJECTION_STATE_INVALID",
        ),
    ],
)
def test_reprojection_rejects_wrong_department_source_or_state(
    wrong_department: bool,
    source_type: ImportSourceType,
    job_status: ImportJobStatus,
    expected_code: str,
) -> None:
    async def scenario() -> None:
        async with database_session() as session:
            fixture = await seed_reprojection_fixture(
                session, source_type=source_type, job_status=job_status
            )
            department_id = uuid4() if wrong_department else fixture.department_id
            with pytest.raises(ImportDomainError) as error:
                await HuitunReprojectionService(session).reproject(
                    department_id=department_id,
                    import_job_id=fixture.import_job_id,
                )
            assert error.value.code == expected_code
            assert await count(session, InfluencerMetricSnapshot) == 0
            assert await count(session, InfluencerCurrentMetrics) == 0

    asyncio.run(scenario())


def test_reprojection_scopes_to_committed_rows_in_the_confirmed_revision_only() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            fixture = await seed_reprojection_fixture(session)
            job = await session.get(ImportJob, fixture.import_job_id)
            assert job is not None
            import_file = await session.scalar(
                select(ImportJobFile).where(ImportJobFile.import_job_id == fixture.import_job_id)
            )
            assert import_file is not None
            session.add_all(
                (
                    ImportRow(
                        import_job_id=job.id,
                        import_job_file_id=import_file.id,
                        row_number=3,
                        raw_data={**RAW_DATA, "粉丝数": "53.0"},
                        normalized_data=None,
                        matched_influencer_id=fixture.influencer_id,
                        matched_platform_account_id=fixture.account_id,
                        match_type=ImportMatchType.PLATFORM_ACCOUNT_ID,
                        action=ImportRowAction.CREATE,
                        merge_plan={},
                        warnings=[],
                        errors=[],
                        preview_revision=3,
                        plan_hash="b" * 64,
                        committed_action=ImportRowAction.CREATE,
                        committed_at=None,
                    ),
                    ImportRow(
                        import_job_id=job.id,
                        import_job_file_id=import_file.id,
                        row_number=4,
                        raw_data={**RAW_DATA, "粉丝数": "6228903.0"},
                        normalized_data=None,
                        matched_influencer_id=fixture.influencer_id,
                        matched_platform_account_id=fixture.account_id,
                        match_type=ImportMatchType.PLATFORM_ACCOUNT_ID,
                        action=ImportRowAction.CREATE,
                        merge_plan={},
                        warnings=[],
                        errors=[],
                        preview_revision=2,
                        plan_hash="c" * 64,
                        committed_action=ImportRowAction.CREATE,
                        committed_at=COMMITTED_AT,
                    ),
                )
            )
            await session.commit()

            result = await HuitunReprojectionService(session).reproject(
                department_id=fixture.department_id,
                import_job_id=fixture.import_job_id,
            )

            assert result["selected_committed_rows"] == 1
            assert (await source_state_for(session, fixture)).source_data[
                "creator_classification_tags"
            ] == ["舞蹈"]
            assert (await session.get(ImportRow, fixture.row_id)).raw_data == RAW_DATA  # type: ignore[union-attr]
            assert await count(session, InfluencerMetricSnapshot) == 1

    asyncio.run(scenario())


def test_reprojection_never_calls_the_matcher_or_creates_identity() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            fixture = await seed_reprojection_fixture(session)
            with patch.object(
                ImportPlanner,
                "_match",
                new=AsyncMock(side_effect=AssertionError("matcher must not run")),
            ) as matcher:
                await HuitunReprojectionService(session).reproject(
                    department_id=fixture.department_id,
                    import_job_id=fixture.import_job_id,
                )
            matcher.assert_not_awaited()
            assert await count(session, Influencer) == 1
            assert await count(session, InfluencerPlatformAccount) == 1

    asyncio.run(scenario())


def test_reprojection_existing_matching_snapshot_is_immutable_noop_and_is_idempotent() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            fixture = await seed_reprojection_fixture(session)
            row = await session.get(ImportRow, fixture.row_id)
            account = await session.get(InfluencerPlatformAccount, fixture.account_id)
            assert row is not None and account is not None
            record = HuitunReprojectionService._normalize_stored_row(row)
            expected = metric_snapshot_projection(record)
            assert expected is not None
            snapshot = InfluencerMetricSnapshot(
                influencer_id=fixture.influencer_id,
                platform_account_id=fixture.account_id,
                source=DataSource.HUITUN,
                source_updated_at=None,
                import_job_id=fixture.import_job_id,
                import_row_id=fixture.row_id,
                captured_at=COMMITTED_AT,
                metrics=expected["metrics"],
                metrics_hash=expected["metrics_hash"],
                snapshot_key=expected["snapshot_key"],
            )
            session.add(snapshot)
            await session.commit()
            snapshot_before = (
                snapshot.id,
                snapshot.source_updated_at,
                snapshot.captured_at,
                dict(snapshot.metrics),
                snapshot.metrics_hash,
                snapshot.snapshot_key,
            )

            first = await HuitunReprojectionService(session).reproject(
                department_id=fixture.department_id,
                import_job_id=fixture.import_job_id,
            )
            snapshot_after_first = await session.get(InfluencerMetricSnapshot, snapshot.id)
            assert snapshot_after_first is not None
            assert (
                snapshot_after_first.id,
                snapshot_after_first.source_updated_at,
                as_utc(snapshot_after_first.captured_at),
                dict(snapshot_after_first.metrics),
                snapshot_after_first.metrics_hash,
                snapshot_after_first.snapshot_key,
            ) == (
                snapshot_before[0],
                snapshot_before[1],
                as_utc(snapshot_before[2]),
                snapshot_before[3],
                snapshot_before[4],
                snapshot_before[5],
            )
            assert first["metric_snapshots_inserted"] == 0
            assert await count(session, InfluencerMetricSnapshot) == 1

            second = await HuitunReprojectionService(session).reproject(
                department_id=fixture.department_id,
                import_job_id=fixture.import_job_id,
            )
            assert second["source_states_repaired"] == 0
            assert second["metric_snapshots_inserted"] == 0
            assert await count(session, InfluencerMetricSnapshot) == 1
            assert await count(session, Influencer) == 1
            assert await count(session, InfluencerPlatformAccount) == 1

    asyncio.run(scenario())


def test_reprojection_obeys_existing_cross_row_snapshot_dedupe() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            fixture = await seed_reprojection_fixture(session)
            job = await session.get(ImportJob, fixture.import_job_id)
            row = await session.get(ImportRow, fixture.row_id)
            import_file = await session.scalar(
                select(ImportJobFile).where(ImportJobFile.import_job_id == fixture.import_job_id)
            )
            assert job is not None and row is not None and import_file is not None
            record = HuitunReprojectionService._normalize_stored_row(row)
            expected = metric_snapshot_projection(record)
            assert expected is not None
            other_row = ImportRow(
                id=uuid4(),
                import_job_id=job.id,
                import_job_file_id=import_file.id,
                row_number=3,
                raw_data=dict(row.raw_data),
                normalized_data=None,
                matched_influencer_id=fixture.influencer_id,
                matched_platform_account_id=fixture.account_id,
                match_type=ImportMatchType.PLATFORM_ACCOUNT_ID,
                action=ImportRowAction.NO_CHANGE,
                merge_plan={},
                warnings=[],
                errors=[],
                preview_revision=job.confirmed_revision or 3,
                plan_hash="f" * 64,
                committed_action=ImportRowAction.NO_CHANGE,
                committed_at=COMMITTED_AT,
            )
            session.add(other_row)
            await session.flush()
            session.add(
                InfluencerMetricSnapshot(
                    influencer_id=fixture.influencer_id,
                    platform_account_id=fixture.account_id,
                    source=DataSource.HUITUN,
                    source_updated_at=None,
                    import_job_id=job.id,
                    import_row_id=other_row.id,
                    captured_at=COMMITTED_AT,
                    metrics=expected["metrics"],
                    metrics_hash=expected["metrics_hash"],
                    snapshot_key=expected["snapshot_key"],
                )
            )
            await session.commit()

            result = await HuitunReprojectionService(session).reproject(
                department_id=fixture.department_id,
                import_job_id=fixture.import_job_id,
            )

            assert result["selected_committed_rows"] == 2
            assert result["metric_snapshots_inserted"] == 0
            assert await session.get(InfluencerMetricSnapshot, fixture.row_id) is None
            assert await count(session, InfluencerMetricSnapshot) == 1

    asyncio.run(scenario())


def test_reprojection_snapshot_conflict_rolls_back_without_update_or_delete() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            fixture = await seed_reprojection_fixture(session)
            snapshot = InfluencerMetricSnapshot(
                influencer_id=fixture.influencer_id,
                platform_account_id=fixture.account_id,
                source=DataSource.HUITUN,
                source_updated_at=None,
                import_job_id=fixture.import_job_id,
                import_row_id=fixture.row_id,
                captured_at=COMMITTED_AT,
                metrics={"followers_count": 1},
                metrics_hash=hash_document({"followers_count": 1}),
                snapshot_key="d" * 64,
            )
            session.add(snapshot)
            await session.commit()
            snapshot_id = snapshot.id
            before = (
                snapshot_id,
                dict(snapshot.metrics),
                snapshot.metrics_hash,
                snapshot.snapshot_key,
            )

            with pytest.raises(ImportDomainError) as error:
                await HuitunReprojectionService(session).reproject(
                    department_id=fixture.department_id,
                    import_job_id=fixture.import_job_id,
                )
            assert error.value.code == "HUITUN_REPROJECTION_SNAPSHOT_CONFLICT"
            restored = await session.get(InfluencerMetricSnapshot, snapshot_id)
            state = await source_state_for(session, fixture)
            assert restored is not None
            assert (
                restored.id,
                dict(restored.metrics),
                restored.metrics_hash,
                restored.snapshot_key,
            ) == before
            assert "creator_classification_tags" not in state.source_data
            assert await count(session, InfluencerMetricSnapshot) == 1
            assert await count(session, InfluencerCurrentMetrics) == 0

    asyncio.run(scenario())


def test_reprojection_snapshot_without_current_metrics_fails_closed() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            fixture = await seed_reprojection_fixture(session, followers="871798.5")
            snapshot = InfluencerMetricSnapshot(
                influencer_id=fixture.influencer_id,
                platform_account_id=fixture.account_id,
                source=DataSource.HUITUN,
                source_updated_at=None,
                import_job_id=fixture.import_job_id,
                import_row_id=fixture.row_id,
                captured_at=COMMITTED_AT,
                metrics={"followers_count": 871798},
                metrics_hash=hash_document({"followers_count": 871798}),
                snapshot_key="e" * 64,
            )
            session.add(snapshot)
            await session.commit()

            with pytest.raises(ImportDomainError) as error:
                await HuitunReprojectionService(session).reproject(
                    department_id=fixture.department_id,
                    import_job_id=fixture.import_job_id,
                )
            assert error.value.code == "HUITUN_REPROJECTION_SNAPSHOT_CONFLICT"
            assert await count(session, InfluencerMetricSnapshot) == 1
            assert await count(session, InfluencerCurrentMetrics) == 0

    asyncio.run(scenario())


def test_reprojection_invalid_fractional_metrics_do_not_create_a_snapshot() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            fixture = await seed_reprojection_fixture(session, followers="871798.5")

            result = await HuitunReprojectionService(session).reproject(
                department_id=fixture.department_id,
                import_job_id=fixture.import_job_id,
            )

            assert result["metric_snapshots_inserted"] == 0
            assert result["metric_snapshots_noop"] == 1
            assert await count(session, InfluencerMetricSnapshot) == 0
            assert await count(session, InfluencerCurrentMetrics) == 0
            assert (await source_state_for(session, fixture)).source_data[
                "creator_classification_tags"
            ] == ["舞蹈"]

    asyncio.run(scenario())


def test_reprojection_identity_conflict_fails_closed_without_creating_replacement() -> None:
    async def scenario() -> None:
        async with database_session() as session:
            fixture = await seed_reprojection_fixture(session)
            row = await session.get(ImportRow, fixture.row_id)
            assert row is not None
            replacement = Influencer(display_name="other")
            session.add(replacement)
            await session.flush()
            replacement_account = InfluencerPlatformAccount(
                influencer_id=replacement.id,
                platform=Platform.DOUYIN,
                platform_account_id="other-token",
                account_name="other",
                source=DataSource.HUITUN,
                is_active=True,
            )
            session.add(replacement_account)
            await session.flush()
            row.matched_influencer_id = replacement.id
            row.matched_platform_account_id = replacement_account.id
            await session.commit()

            with pytest.raises(ImportDomainError) as error:
                await HuitunReprojectionService(session).reproject(
                    department_id=fixture.department_id,
                    import_job_id=fixture.import_job_id,
                )
            assert error.value.code == "HUITUN_REPROJECTION_IDENTITY_CONFLICT"
            assert await count(session, Influencer) == 2
            assert await count(session, InfluencerPlatformAccount) == 2
            assert await count(session, InfluencerMetricSnapshot) == 0

    asyncio.run(scenario())


def test_normal_confirm_apply_never_enters_reprojection_reconcile_branch() -> None:
    async def scenario() -> None:
        session = MagicMock(spec=AsyncSession)
        influencer_id = uuid4()
        account_id = uuid4()
        account = InfluencerPlatformAccount(
            id=account_id,
            influencer_id=influencer_id,
            platform=Platform.DOUYIN,
            platform_account_id="normal-confirm-token",
            account_name="normal confirm",
            source=DataSource.HUITUN,
            is_active=True,
        )
        cache = MergeApplyCache(
            accounts_by_id={account_id: account},
            source_states={},
            current_metrics={},
            contacts_by_id={},
            covered_account_sources={AccountSourceKey(account_id, DataSource.HUITUN)},
        )
        plan = PlannedImportRow(
            matched_influencer_id=influencer_id,
            matched_platform_account_id=account_id,
            match_type=ImportMatchType.PLATFORM_ACCOUNT_ID,
            action=ImportRowAction.NO_CHANGE,
            merge_plan={
                "account_create": None,
                "account_updates": {},
                "source_identity": None,
                "source_state": None,
                "contacts": {
                    "deactivate_ids": [],
                    "mark_duplicate_ids": [],
                    "observe_ids": [],
                    "create": [],
                },
                "metrics": {"current": None, "snapshot": None},
            },
            warnings=[],
            errors=[],
            preconditions={},
            plan_hash="0" * 64,
        )
        record = CanonicalInfluencerRecord(
            display_name="normal confirm",
            platform_identity=PlatformIdentity(
                platform=Platform.DOUYIN,
                platform_account_id="normal-confirm-token",
                account_handle=None,
                profile_url=None,
                normalized_profile_url=None,
                external_source_id=None,
            ),
            source=DataSource.HUITUN,
            source_updated_at=COMMITTED_AT,
        )
        applier = ImportMergeApplier(session, cache=cache)
        with patch.object(
            applier,
            "reproject_source_state_and_metrics",
            new=AsyncMock(side_effect=AssertionError("repair branch must not run")),
        ) as repair:
            await applier.apply(
                ImportJob(id=uuid4(), operator_id=uuid4()),
                ImportRow(id=uuid4(), row_number=2),
                record,
                plan,
                COMMITTED_AT,
            )
        repair.assert_not_awaited()

    asyncio.run(scenario())


def test_reprojection_cli_requires_exact_ids_and_propagates_conflicts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as missing_department:
        parse_args(["--import-job-id", str(uuid4())])
    assert missing_department.value.code == 2
    with pytest.raises(SystemExit) as missing_job:
        parse_args(["--department-id", str(uuid4())])
    assert missing_job.value.code == 2

    department_id = uuid4()
    job_id = uuid4()
    summary = {
        "department_id": str(department_id),
        "import_job_id": str(job_id),
        "selected_committed_rows": 1,
        "processed": 1,
        "source_states_repaired": 1,
        "source_states_noop": 0,
        "metric_snapshots_inserted": 1,
        "metric_snapshots_noop": 0,
        "conflicts": 0,
        "errors": 0,
    }
    with patch(
        "backend_core.imports.huitun_reprojection_cli.reproject",
        new=AsyncMock(return_value=summary),
    ) as service:
        main(["--department-id", str(department_id), "--import-job-id", str(job_id)])
    service.assert_awaited_once_with(department_id=department_id, import_job_id=job_id)
    assert f"job id: {job_id}" in capsys.readouterr().out

    with patch(
        "backend_core.imports.huitun_reprojection_cli.reproject",
        new=AsyncMock(
            side_effect=ImportDomainError(
                "HUITUN_REPROJECTION_SNAPSHOT_CONFLICT", "snapshot conflict", status_code=409
            )
        ),
    ):
        with pytest.raises(SystemExit) as conflict:
            main(["--department-id", str(department_id), "--import-job-id", str(job_id)])
    assert conflict.value.code == 1
    assert "HUITUN_REPROJECTION_SNAPSHOT_CONFLICT" in capsys.readouterr().err
