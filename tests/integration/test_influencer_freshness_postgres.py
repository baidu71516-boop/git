"""Opt-in PostgreSQL 16 gates for Phase 2 influencer freshness.

The module inherits the existing destructive-test safety gate from the Phase 2
PostgreSQL harness: ``TEST_DATABASE_URL`` must name PostgreSQL and its database
name must contain ``phase1b_test``.  Every scenario creates and drops a random
schema.  All data is synthetic.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import test_bulk_confirm_postgres as confirm_gate
import test_unified_preview_postgres as preview_gate
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, Operator
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
from backend_core.imports.mappings import HUITUN_FIELD_MAPPING
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportRow,
    StoredImportFile,
)
from backend_core.influencers.enums import CRMStage, DataSource, Platform
from backend_core.influencers.freshness import (
    ContentActivityFreshnessPolicy,
    FreshnessPolicy,
    FreshnessStatus,
)
from backend_core.influencers.models import (
    Influencer,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
)
from backend_core.influencers.repository import (
    AccountSourceFreshnessRecord,
    InfluencerListRecord,
    InfluencerRepository,
)
from backend_core.influencers.schemas import InfluencerListQuery
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

AS_OF = datetime(2026, 8, 13, 6, 0, tzinfo=UTC)
POLICY = FreshnessPolicy.from_day_thresholds(7, 30, 90)
HUITUN_HEADERS = (*HUITUN_FIELD_MAPPING, "fixture_nonce")
HUITUN_MAPPING = dict(HUITUN_FIELD_MAPPING)


@dataclass(slots=True)
class _LineageBundle:
    job: ImportJob
    occurrence: ImportJobFile
    next_row_number: int = 2


def _huitun_row(
    identity: str,
    *,
    source_updated_at: str = "2026-06-01 12:00:00",
    followers: str = "200",
    nonce: str,
) -> dict[str, str]:
    values = {header: "--" for header in HUITUN_FIELD_MAPPING}
    values.update(
        {
            "达人名称": f"Freshness {identity}",
            "达人官方地址": f"https://www.xiaohongshu.com/user/profile/{identity}",
            "更新时间": source_updated_at,
            "粉丝数": followers,
            "达人标签": "freshness-gate",
            "fixture_nonce": nonce,
        }
    )
    return values


async def _set_acquisition_times(
    harness: preview_gate.PostgresHarness,
    seeded: preview_gate.SeededBatch,
    values: tuple[datetime, ...],
) -> None:
    assert len(seeded.files) == len(values)
    async with harness.factory() as session:
        for seeded_file, acquired_at in zip(seeded.files, values, strict=True):
            occurrence = await session.get(ImportJobFile, seeded_file.id)
            assert occurrence is not None
            occurrence.source_acquired_at = acquired_at
            occurrence.source_acquired_at_origin = SourceAcquiredAtOrigin.USER_CONFIRMED
            occurrence.source_acquired_at_confirmation_required = False
        await session.commit()


async def _confirm_huitun_batch(
    harness: preview_gate.PostgresHarness,
    files: list[list[dict[str, str]]],
    *,
    acquired_at: tuple[datetime, ...],
) -> preview_gate.SeededBatch:
    seeded = await preview_gate._seed_batch(
        harness,
        files,
        source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
        field_mapping=HUITUN_MAPPING,
        csv_headers=HUITUN_HEADERS,
    )
    await _set_acquisition_times(harness, seeded, acquired_at)
    await preview_gate._parse_all(harness, seeded)
    revision = await confirm_gate._preview(harness, seeded)
    await confirm_gate._run_confirm(harness, seeded, revision)
    return seeded


async def _seed_operators(session: AsyncSession) -> tuple[Operator, Operator]:
    department = Department(
        name=f"Task 7 freshness {uuid4().hex}",
        password_hash="not-a-real-password-hash",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    primary = Operator(
        department_id=department.id,
        name="Freshness primary owner",
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    secondary = Operator(
        department_id=department.id,
        name="Freshness secondary owner",
        role=Role.VIEWER,
        status=OperatorStatus.ACTIVE,
    )
    session.add_all([primary, secondary])
    await session.flush()
    return primary, secondary


async def _add_influencer(
    session: AsyncSession,
    owner: Operator,
    name: str,
    *,
    influencer_id: UUID | None = None,
    created_at: datetime | None = None,
) -> Influencer:
    influencer = Influencer(
        display_name=name,
        owner_operator_id=owner.id,
        crm_stage=CRMStage.TO_DEVELOP,
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
    label: str,
    *,
    active: bool = True,
    source: DataSource = DataSource.HUITUN,
    source_tags: list[str] | None = None,
) -> InfluencerPlatformAccount:
    identity = f"{label}-{uuid4().hex}"
    account = InfluencerPlatformAccount(
        influencer_id=influencer.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id=identity,
        account_name=label,
        account_handle=identity,
        profile_url=f"https://example.invalid/{identity}",
        normalized_profile_url=f"https://example.invalid/{identity}",
        source=source,
        is_active=active,
        source_tags=source_tags,
    )
    session.add(account)
    await session.flush()
    return account


async def _new_lineage_bundle(
    session: AsyncSession,
    owner: Operator,
    *,
    acquired_at: datetime | None,
    source_type: ImportSourceType = ImportSourceType.MANUAL_HUITUN_EXPORT,
    confirmation_required: bool = False,
    legacy: bool = False,
    job_status: ImportJobStatus = ImportJobStatus.COMPLETED,
    file_status: ImportJobFileStatus = ImportJobFileStatus.READY,
    confirmed_revision: int | None = 1,
) -> _LineageBundle:
    token = uuid4().hex
    collection = CollectionJob(
        name=f"Task 7 lineage {token}",
        industry="synthetic",
        purpose="freshness PostgreSQL gate",
        target_action="read",
        target_count=10_000,
        department_id=owner.department_id,
        owner_operator_id=owner.id,
        source_type=source_type,
        status=CollectionJobStatus.COMPLETED,
    )
    stored_file = StoredImportFile(
        sha256=token * 2,
        storage_key=f"task7/{token}.csv",
        size=1,
        detected_type=StoredFileType.CSV,
        detected_mime="text/csv",
        encoding="utf-8",
        expires_at=AS_OF + timedelta(days=30),
    )
    session.add_all([collection, stored_file])
    await session.flush()
    job = ImportJob(
        collection_job_id=collection.id,
        department_id=owner.department_id,
        operator_id=owner.id,
        stored_file_id=stored_file.id if legacy else None,
        original_filename=f"{token}.csv" if legacy else None,
        mime_type="text/csv" if legacy else None,
        file_size=1 if legacy else None,
        sha256=stored_file.sha256 if legacy else None,
        source_type=source_type,
        status=job_status,
        preview_revision=1,
        confirmed_revision=confirmed_revision,
        confirmed_at=AS_OF - timedelta(hours=2) if confirmed_revision is not None else None,
        completed_at=(
            AS_OF - timedelta(hours=1) if job_status is ImportJobStatus.COMPLETED else None
        ),
    )
    session.add(job)
    await session.flush()
    occurrence = ImportJobFile(
        import_job_id=job.id,
        stored_file_id=stored_file.id,
        position=1,
        original_filename=f"{token}.csv",
        declared_mime="text/csv",
        status=file_status,
        source_acquired_at=acquired_at,
        source_acquired_at_origin=(
            SourceAcquiredAtOrigin.LEGACY_UNKNOWN
            if acquired_at is None
            else (
                SourceAcquiredAtOrigin.SERVER_DEFAULT
                if confirmation_required
                else SourceAcquiredAtOrigin.USER_CONFIRMED
            )
        ),
        source_acquired_at_confirmation_required=confirmation_required,
    )
    session.add(occurrence)
    await session.flush()
    return _LineageBundle(job=job, occurrence=occurrence)


def _add_lineage_row(
    session: AsyncSession,
    bundle: _LineageBundle,
    account: InfluencerPlatformAccount,
    *,
    committed_at: datetime | None,
    action: ImportRowAction = ImportRowAction.NO_CHANGE,
    committed_action: ImportRowAction | None = None,
    preview_revision: int = 1,
) -> ImportRow:
    row_number = bundle.next_row_number
    bundle.next_row_number += 1
    resolved_committed_action = (
        action if committed_action is None and committed_at else (committed_action)
    )
    row = ImportRow(
        import_job_id=bundle.job.id,
        import_job_file_id=bundle.occurrence.id,
        row_number=row_number,
        raw_data={"fixture_row": row_number},
        normalized_data={"fixture_row": row_number},
        matched_influencer_id=account.influencer_id,
        matched_platform_account_id=account.id,
        match_type=ImportMatchType.PLATFORM_ACCOUNT_ID,
        action=action,
        warnings=[],
        errors=[],
        preview_revision=preview_revision,
        plan_hash=f"{bundle.job.id.int ^ row_number:064x}"[-64:],
        committed_action=resolved_committed_action,
        committed_at=committed_at,
    )
    session.add(row)
    return row


def _record_by_name(records: list[InfluencerListRecord]) -> dict[str, InfluencerListRecord]:
    return {record.influencer.display_name: record for record in records}


def _freshness_by_account(
    record: InfluencerListRecord,
) -> dict[UUID, AccountSourceFreshnessRecord]:
    return {item.platform_account_id: item for item in record.huitun_freshness}


def test_confirmed_lineage_legacy_source_isolation_inactive_and_multi_account() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness, harness.factory() as session:
            primary, _ = await _seed_operators(session)

            multi = await _add_influencer(session, primary, "multi-account")
            multi_fresh = await _add_account(session, multi, "multi-fresh")
            multi_stale = await _add_account(session, multi, "multi-stale")
            multi_inactive = await _add_account(
                session,
                multi,
                "multi-inactive",
                active=False,
            )
            fresh_bundle = await _new_lineage_bundle(
                session,
                primary,
                acquired_at=AS_OF - timedelta(days=1),
            )
            stale_bundle = await _new_lineage_bundle(
                session,
                primary,
                acquired_at=AS_OF - timedelta(days=60),
            )
            inactive_bundle = await _new_lineage_bundle(
                session,
                primary,
                acquired_at=AS_OF - timedelta(days=120),
            )
            _add_lineage_row(
                session,
                fresh_bundle,
                multi_fresh,
                committed_at=AS_OF - timedelta(hours=6),
            )
            _add_lineage_row(
                session,
                stale_bundle,
                multi_stale,
                committed_at=AS_OF - timedelta(hours=5),
            )
            _add_lineage_row(
                session,
                inactive_bundle,
                multi_inactive,
                committed_at=AS_OF - timedelta(hours=4),
            )

            active_only = await _add_influencer(session, primary, "fresh-with-inactive")
            active_fresh = await _add_account(session, active_only, "active-fresh")
            inactive_stale = await _add_account(
                session,
                active_only,
                "inactive-stale",
                active=False,
            )
            _add_lineage_row(
                session,
                fresh_bundle,
                active_fresh,
                committed_at=AS_OF - timedelta(hours=3),
            )
            _add_lineage_row(
                session,
                inactive_bundle,
                inactive_stale,
                committed_at=AS_OF - timedelta(hours=2),
            )

            all_fresh = await _add_influencer(session, primary, "all-fresh")
            all_fresh_one = await _add_account(session, all_fresh, "all-fresh-one")
            all_fresh_two = await _add_account(session, all_fresh, "all-fresh-two")
            _add_lineage_row(
                session,
                fresh_bundle,
                all_fresh_one,
                committed_at=AS_OF - timedelta(hours=2),
            )
            _add_lineage_row(
                session,
                fresh_bundle,
                all_fresh_two,
                committed_at=AS_OF - timedelta(hours=1),
            )

            legacy = await _add_influencer(session, primary, "legacy-unknown")
            legacy_account = await _add_account(session, legacy, "legacy-account")
            legacy_imported_at = AS_OF - timedelta(minutes=50)
            legacy_bundle = await _new_lineage_bundle(
                session,
                primary,
                acquired_at=None,
                legacy=True,
            )
            _add_lineage_row(
                session,
                legacy_bundle,
                legacy_account,
                committed_at=legacy_imported_at,
                action=ImportRowAction.CREATE,
            )

            isolated = await _add_influencer(session, primary, "source-isolated")
            isolated_account = await _add_account(session, isolated, "source-isolated-account")
            reliable_observed_at = AS_OF - timedelta(days=60)
            reliable_imported_at = AS_OF - timedelta(minutes=40)
            reliable_bundle = await _new_lineage_bundle(
                session,
                primary,
                acquired_at=reliable_observed_at,
            )
            _add_lineage_row(
                session,
                reliable_bundle,
                isolated_account,
                committed_at=reliable_imported_at,
            )

            generic_bundle = await _new_lineage_bundle(
                session,
                primary,
                acquired_at=AS_OF - timedelta(days=1),
                source_type=ImportSourceType.GENERIC_CSV,
            )
            _add_lineage_row(
                session,
                generic_bundle,
                isolated_account,
                committed_at=AS_OF - timedelta(minutes=30),
            )

            pending_imported_at = AS_OF - timedelta(minutes=20)
            pending_bundle = await _new_lineage_bundle(
                session,
                primary,
                acquired_at=AS_OF,
                confirmation_required=True,
            )
            _add_lineage_row(
                session,
                pending_bundle,
                isolated_account,
                committed_at=pending_imported_at,
            )

            excluded_bundle = await _new_lineage_bundle(
                session,
                primary,
                acquired_at=AS_OF + timedelta(minutes=1),
                file_status=ImportJobFileStatus.EXCLUDED,
            )
            _add_lineage_row(
                session,
                excluded_bundle,
                isolated_account,
                committed_at=AS_OF - timedelta(minutes=10),
            )
            failed_bundle = await _new_lineage_bundle(
                session,
                primary,
                acquired_at=AS_OF + timedelta(minutes=2),
                job_status=ImportJobStatus.FAILED,
            )
            _add_lineage_row(
                session,
                failed_bundle,
                isolated_account,
                committed_at=AS_OF - timedelta(minutes=9),
            )
            ignored_action_bundle = await _new_lineage_bundle(
                session,
                primary,
                acquired_at=AS_OF + timedelta(minutes=3),
            )
            _add_lineage_row(
                session,
                ignored_action_bundle,
                isolated_account,
                committed_at=AS_OF - timedelta(minutes=8),
                action=ImportRowAction.MANUAL_REVIEW,
            )

            no_huitun = await _add_influencer(session, primary, "no-huitun-evidence")
            await _add_account(
                session,
                no_huitun,
                "generic-only",
                source=DataSource.GENERIC,
            )
            await session.commit()

            repository = InfluencerRepository(session)
            records, total = await repository.list_influencers(
                InfluencerListQuery(page_size=100),
                as_of=AS_OF,
                policy=POLICY,
            )
            assert total == 6
            by_name = _record_by_name(records)

            multi_freshness = _freshness_by_account(by_name["multi-account"])
            assert set(multi_freshness) == {multi_fresh.id, multi_stale.id}
            assert multi_freshness[multi_fresh.id].last_observed_at == AS_OF - timedelta(days=1)
            assert multi_freshness[multi_stale.id].last_observed_at == AS_OF - timedelta(days=60)
            assert multi_inactive.id not in multi_freshness

            active_only_freshness = _freshness_by_account(by_name["fresh-with-inactive"])
            assert set(active_only_freshness) == {active_fresh.id}
            assert inactive_stale.id not in active_only_freshness

            legacy_freshness = _freshness_by_account(by_name["legacy-unknown"])
            assert legacy_freshness[legacy_account.id].source is DataSource.HUITUN
            assert legacy_freshness[legacy_account.id].last_observed_at is None
            assert legacy_freshness[legacy_account.id].last_imported_at == legacy_imported_at

            isolated_freshness = _freshness_by_account(by_name["source-isolated"])
            assert isolated_freshness[isolated_account.id].last_observed_at == reliable_observed_at
            assert isolated_freshness[isolated_account.id].last_imported_at == pending_imported_at

            assert by_name["no-huitun-evidence"].huitun_freshness == ()
            assert len(by_name["all-fresh"].huitun_freshness) == 2

            harness.probe.start()
            detail = await repository.get_influencer_detail(multi.id)
            detail_measurement = harness.probe.stop(rows=1)
            assert detail is not None
            assert {item.platform_account_id for item in detail.huitun_freshness} == {
                multi_fresh.id,
                multi_stale.id,
            }
            # D1A adds one bounded, set-wise projection-map read for the
            # platform-neutral Content Activity fields.  It must remain a
            # fixed detail-read cost rather than an account-by-account load.
            assert detail_measurement.total == 8
            assert detail_measurement.dml == 0

            async def names_for(query: InfluencerListQuery) -> set[str]:
                matched, _ = await repository.list_influencers(
                    query,
                    as_of=AS_OF,
                    policy=POLICY,
                )
                return {record.influencer.display_name for record in matched}

            assert await names_for(
                InfluencerListQuery(freshness_status=FreshnessStatus.UNKNOWN)
            ) == {"legacy-unknown"}
            assert await names_for(InfluencerListQuery(freshness_status=FreshnessStatus.STALE)) == {
                "multi-account",
                "source-isolated",
            }
            assert await names_for(InfluencerListQuery(requires_refresh=True)) == {
                "legacy-unknown",
                "multi-account",
                "source-isolated",
            }
            assert await names_for(InfluencerListQuery(requires_refresh=False)) == {
                "all-fresh",
                "fresh-with-inactive",
                "no-huitun-evidence",
            }
            assert (
                await names_for(InfluencerListQuery(freshness_status=FreshnessStatus.VERY_STALE))
                == set()
            )
            assert await names_for(
                InfluencerListQuery(
                    last_huitun_observed_after=reliable_observed_at,
                    last_huitun_observed_before=reliable_observed_at,
                )
            ) == {"multi-account", "source-isolated"}

    asyncio.run(scenario())


def test_real_bulk_confirm_same_metrics_and_older_metrics_advance_observation() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            identity = "task7-repeat-account"
            other_identity = "task7-second-file-account"
            first_observed = AS_OF - timedelta(days=60)
            other_observed = AS_OF - timedelta(days=15)
            first = await _confirm_huitun_batch(
                harness,
                [
                    [_huitun_row(identity, nonce="initial")],
                    [_huitun_row(other_identity, followers="300", nonce="second-file")],
                ],
                acquired_at=(first_observed, other_observed),
            )
            async with harness.factory() as session:
                first_rows = await preview_gate._job_rows(session, first.job_id)
                assert len(first_rows) == 2
                assert all(row.committed_action is ImportRowAction.CREATE for row in first_rows)
                account = await session.scalar(
                    select(InfluencerPlatformAccount).where(
                        InfluencerPlatformAccount.normalized_profile_url
                        == f"https://www.xiaohongshu.com/user/profile/{identity}"
                    )
                )
                assert account is not None
                first_snapshot_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(InfluencerMetricSnapshot)
                        .where(InfluencerMetricSnapshot.platform_account_id == account.id)
                    )
                    or 0
                )
                assert first_snapshot_count == 1
                first_records, _ = await InfluencerRepository(session).list_influencers(
                    InfluencerListQuery(q="task7-"),
                    as_of=AS_OF,
                    policy=POLICY,
                )
                first_by_name = _record_by_name(first_records)
                first_record = first_by_name[f"Freshness {identity}"]
                other_record = first_by_name[f"Freshness {other_identity}"]
                other_account = next(
                    item
                    for item in other_record.platform_accounts
                    if item.normalized_profile_url
                    == f"https://www.xiaohongshu.com/user/profile/{other_identity}"
                )
                assert (
                    _freshness_by_account(other_record)[other_account.id].last_observed_at
                    == other_observed
                )
                initial_freshness = _freshness_by_account(first_record)[account.id]
                assert initial_freshness.last_observed_at == first_observed
                assert initial_freshness.last_imported_at is not None

            same_observed = AS_OF - timedelta(days=1)
            repeated = await _confirm_huitun_batch(
                harness,
                [[_huitun_row(identity, nonce="same-metrics-new-observation")]],
                acquired_at=(same_observed,),
            )
            async with harness.factory() as session:
                repeated_rows = await preview_gate._job_rows(session, repeated.job_id)
                assert len(repeated_rows) == 1
                assert repeated_rows[0].committed_action is ImportRowAction.NO_CHANGE
                assert repeated_rows[0].matched_platform_account_id == account.id
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(InfluencerMetricSnapshot)
                        .where(InfluencerMetricSnapshot.platform_account_id == account.id)
                    )
                    == first_snapshot_count
                )
                records, _ = await InfluencerRepository(session).list_influencers(
                    InfluencerListQuery(q=identity),
                    as_of=AS_OF,
                    policy=POLICY,
                )
                same_freshness = _freshness_by_account(records[0])[account.id]
                assert same_freshness.last_observed_at == same_observed
                assert same_freshness.last_imported_at is not None
                assert same_freshness.last_imported_at >= initial_freshness.last_imported_at

            older_observed = AS_OF
            older = await _confirm_huitun_batch(
                harness,
                [
                    [
                        _huitun_row(
                            identity,
                            source_updated_at="2026-05-01 12:00:00",
                            followers="999",
                            nonce="older-metrics-new-observation",
                        )
                    ]
                ],
                acquired_at=(older_observed,),
            )
            async with harness.factory() as session:
                older_rows = await preview_gate._job_rows(session, older.job_id)
                assert len(older_rows) == 1
                assert older_rows[0].committed_action is ImportRowAction.NO_CHANGE
                assert "STALE_METRICS_CURRENT_IGNORED" in {
                    warning["code"] for warning in older_rows[0].warnings
                }
                current = await session.scalar(
                    select(InfluencerCurrentMetrics).where(
                        InfluencerCurrentMetrics.platform_account_id == account.id,
                        InfluencerCurrentMetrics.source == DataSource.HUITUN,
                    )
                )
                assert current is not None
                assert current.metrics["followers_count"] == 200
                assert current.source_updated_at == datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
                records, _ = await InfluencerRepository(session).list_influencers(
                    InfluencerListQuery(q=identity),
                    as_of=AS_OF,
                    policy=POLICY,
                )
                older_freshness = _freshness_by_account(records[0])[account.id]
                assert older_freshness.last_observed_at == older_observed
                assert older_freshness.last_imported_at is not None
                assert older_freshness.last_imported_at >= same_freshness.last_imported_at

    asyncio.run(scenario())


async def _seed_scale(
    session: AsyncSession,
    *,
    size: int,
) -> tuple[Operator, list[UUID]]:
    primary, secondary = await _seed_operators(session)
    observed_times = (
        AS_OF - timedelta(days=1),
        AS_OF - timedelta(days=15),
        AS_OF - timedelta(days=60),
        AS_OF - timedelta(days=120),
        None,
    )
    bundles = [
        await _new_lineage_bundle(
            session,
            primary,
            acquired_at=observed_at,
            legacy=observed_at is None,
        )
        for observed_at in observed_times
    ]
    shared_created_at = AS_OF - timedelta(days=365)
    influencers: list[Influencer] = []
    accounts: list[InfluencerPlatformAccount] = []
    for index in range(size):
        influencer_id = UUID(int=index + 1)
        account_id = UUID(int=100_000 + index + 1)
        owner = primary if index % 2 == 0 else secondary
        influencer = Influencer(
            id=influencer_id,
            display_name=f"Scale freshness {index:05d}",
            owner_operator_id=owner.id,
            crm_stage=CRMStage.TO_DEVELOP,
            created_at=shared_created_at,
            updated_at=shared_created_at,
        )
        account = InfluencerPlatformAccount(
            id=account_id,
            influencer_id=influencer_id,
            platform=Platform.XIAOHONGSHU,
            platform_account_id=f"scale-{index:05d}",
            account_name=f"Scale account {index:05d}",
            account_handle=f"scale-{index:05d}",
            profile_url=f"https://example.invalid/scale/{index:05d}",
            normalized_profile_url=f"https://example.invalid/scale/{index:05d}",
            source=DataSource.HUITUN,
            is_active=True,
            source_tags=["cohort", f"bucket-{index % 5}"],
        )
        influencers.append(influencer)
        accounts.append(account)
    session.add_all(influencers)
    await session.flush()
    session.add_all(accounts)
    await session.flush()

    rows: list[ImportRow] = []
    for index, account in enumerate(accounts):
        bundle = bundles[index % len(bundles)]
        row_number = bundle.next_row_number
        bundle.next_row_number += 1
        rows.append(
            ImportRow(
                id=UUID(int=200_000 + index + 1),
                import_job_id=bundle.job.id,
                import_job_file_id=bundle.occurrence.id,
                row_number=row_number,
                raw_data={"scale": index},
                normalized_data={"scale": index},
                matched_influencer_id=account.influencer_id,
                matched_platform_account_id=account.id,
                match_type=ImportMatchType.PLATFORM_ACCOUNT_ID,
                action=ImportRowAction.NO_CHANGE,
                warnings=[],
                errors=[],
                preview_revision=1,
                plan_hash=f"{index + 1:064x}",
                committed_action=ImportRowAction.NO_CHANGE,
                committed_at=AS_OF - timedelta(minutes=1),
            )
        )
    session.add_all(rows)
    await session.flush()
    session.add_all(
        [
            InfluencerCurrentMetrics(
                id=UUID(int=300_000 + index + 1),
                influencer_id=account.influencer_id,
                platform_account_id=account.id,
                source=DataSource.HUITUN,
                source_updated_at=AS_OF - timedelta(days=1),
                metrics={"followers_count": 10_000 + index},
                metrics_hash=f"{400_000 + index + 1:064x}",
                last_import_job_id=rows[index].import_job_id,
                last_import_row_id=rows[index].id,
            )
            for index, account in enumerate(accounts)
        ]
    )
    await session.commit()
    return primary, [influencer.id for influencer in influencers]


async def _explain_production_filtered_count(
    session: AsyncSession,
    repository: InfluencerRepository,
    query: InfluencerListQuery,
    *,
    expected_rows: int,
) -> None:
    """EXPLAIN the exact count statement used by the production list path."""

    index_definition = await session.scalar(
        text(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = 'import_rows'
              AND indexname = 'ix_import_rows_account_committed_job'
            """
        )
    )
    assert index_definition is not None
    assert "matched_platform_account_id" in index_definition
    assert "committed_at DESC" in index_definition
    assert "import_job_id" in index_definition
    await session.execute(text("ANALYZE import_rows"))

    criteria = repository._list_criteria(
        query,
        as_of=AS_OF,
        policy=POLICY,
        content_activity_freshness_policy=ContentActivityFreshnessPolicy(),
    )
    statement = select(func.count(Influencer.id)).where(*criteria)
    captured: list[tuple[str, object]] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        sql: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        captured.append((sql, parameters))

    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        assert await session.scalar(statement) is not None
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)
    assert len(captured) == 1
    compiled_sql, processed_parameters = captured[0]
    for required_fragment in (
        "stored_file_id IS NULL",
        "confirmed_revision IS NOT NULL",
        "preview_revision = import_jobs.confirmed_revision",
        "source_acquired_at_confirmation_required IS false",
    ):
        assert required_fragment in compiled_sql
    connection = await session.connection()
    plan_result = await connection.exec_driver_sql(
        f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {compiled_sql}",
        processed_parameters,  # type: ignore[arg-type]
    )
    plan_document = plan_result.scalar_one()
    assert isinstance(plan_document, list) and len(plan_document) == 1
    document = plan_document[0]

    def plan_nodes(node: dict[str, object]) -> list[dict[str, object]]:
        nested = [node]
        for child in node.get("Plans", []):
            assert isinstance(child, dict)
            nested.extend(plan_nodes(child))
        return nested

    root_plan = document["Plan"]
    assert isinstance(root_plan, dict)
    nodes = plan_nodes(root_plan)
    index_names = {str(node["Index Name"]) for node in nodes if "Index Name" in node}
    import_row_index_names = index_names & {
        "ix_import_rows_account_committed_job",
        "ix_import_rows_job_action",
    }
    assert import_row_index_names
    import_row_scans = [node for node in nodes if node.get("Relation Name") == "import_rows"]
    assert import_row_scans, document
    assert (
        sum(
            (int(node.get("Actual Rows", 0)) + int(node.get("Rows Removed by Filter", 0)))
            * int(node.get("Actual Loops", 0))
            for node in import_row_scans
        )
        <= expected_rows + 5
    )
    print(
        "TASK7_FRESHNESS_EXPLAIN_SUMMARY "
        + json.dumps(
            {
                "execution_ms": float(document["Execution Time"]),
                "import_row_indexes": sorted(import_row_index_names),
                "import_row_scan_nodes": [
                    {
                        "node_type": node.get("Node Type"),
                        "actual_rows": node.get("Actual Rows"),
                        "actual_loops": node.get("Actual Loops"),
                        "rows_removed": node.get("Rows Removed by Filter", 0),
                        "shared_hit_blocks": node.get("Shared Hit Blocks", 0),
                    }
                    for node in import_row_scans
                ],
            },
            sort_keys=True,
        )
    )
    assert float(document["Execution Time"]) < 5_000


def test_scale_query_count_pagination_combined_filters_and_explain() -> None:
    async def scenario() -> None:
        statement_counts: list[int] = []
        for size in (50, 500, 2_000):
            async with preview_gate._isolated_postgres() as harness:
                async with harness.factory() as session:
                    primary, influencer_ids = await _seed_scale(session, size=size)
                    repository = InfluencerRepository(session)
                    harness.probe.start()
                    try:
                        first_page, total = await repository.list_influencers(
                            InfluencerListQuery(page=1, page_size=17),
                            as_of=AS_OF,
                            policy=POLICY,
                        )
                    except BaseException:
                        harness.probe.stop(rows=0)
                        raise
                    measurement = harness.probe.stop(rows=len(first_page))
                    statement_counts.append(measurement.total)
                    print(
                        "TASK7_FRESHNESS_SCALE "
                        + json.dumps(
                            {
                                "rows": size,
                                "sql_total": measurement.total,
                                "sql_select": measurement.selects,
                                "sql_dml": measurement.dml,
                                "wall_seconds": round(measurement.wall_seconds, 6),
                            },
                            sort_keys=True,
                        )
                    )
                    assert total == size
                    assert measurement.dml == 0
                    # The Content Activity projection map is one additional
                    # set-wise read, independent of page/result size.
                    assert measurement.total == 7
                    assert measurement.wall_seconds < 10
                    expected_order = list(reversed(influencer_ids))
                    assert [record.influencer.id for record in first_page] == expected_order[:17]

                    repeated_page, repeated_total = await repository.list_influencers(
                        InfluencerListQuery(page=1, page_size=17),
                        as_of=AS_OF,
                        policy=POLICY,
                    )
                    second_page, second_total = await repository.list_influencers(
                        InfluencerListQuery(page=2, page_size=17),
                        as_of=AS_OF,
                        policy=POLICY,
                    )
                    assert repeated_total == second_total == size
                    assert [record.influencer.id for record in repeated_page] == expected_order[:17]
                    assert [record.influencer.id for record in second_page] == expected_order[17:34]
                    assert not (
                        {record.influencer.id for record in first_page}
                        & {record.influencer.id for record in second_page}
                    )

                    if size == 2_000:
                        stale_at = AS_OF - timedelta(days=60)
                        combined_query = InfluencerListQuery(
                            q="scale freshness",
                            tag="cohort",
                            followers_min=10_000,
                            followers_max=12_000,
                            owner_operator_id=primary.id,
                            crm_stage=CRMStage.TO_DEVELOP,
                            freshness_status=FreshnessStatus.STALE,
                            requires_refresh=True,
                            last_huitun_observed_after=stale_at,
                            last_huitun_observed_before=stale_at,
                            page=1,
                            page_size=100,
                        )
                        harness.probe.start()
                        try:
                            combined, combined_total = await repository.list_influencers(
                                combined_query,
                                as_of=AS_OF,
                                policy=POLICY,
                            )
                        except BaseException:
                            harness.probe.stop(rows=0)
                            raise
                        combined_measurement = harness.probe.stop(rows=len(combined))
                        print(
                            "TASK7_FRESHNESS_COMBINED "
                            + json.dumps(
                                {
                                    "rows": size,
                                    "sql_total": combined_measurement.total,
                                    "sql_select": combined_measurement.selects,
                                    "sql_dml": combined_measurement.dml,
                                    "wall_seconds": round(
                                        combined_measurement.wall_seconds,
                                        6,
                                    ),
                                },
                                sort_keys=True,
                            )
                        )
                        assert combined_measurement.dml == 0
                        assert combined_measurement.total == 7
                        assert combined_measurement.wall_seconds < 10
                        expected_combined = [
                            influencer_id
                            for index, influencer_id in enumerate(influencer_ids)
                            if index % 5 == 2 and index % 2 == 0
                        ]
                        assert combined_total == len(expected_combined) == 200
                        assert [record.influencer.id for record in combined] == list(
                            reversed(expected_combined)
                        )[:100]
                        await _explain_production_filtered_count(
                            session,
                            repository,
                            combined_query,
                            expected_rows=size,
                        )

        assert len(set(statement_counts)) == 1, statement_counts

    asyncio.run(scenario())
