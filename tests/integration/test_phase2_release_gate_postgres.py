"""Opt-in PostgreSQL 16 release gates added by Phase 2 Task 11.

The fixtures in this module target risks that the task-level gates deliberately
did not model.  They use the existing random-schema harness and synthetic data;
the explicit environment switch keeps the larger fanout dataset out of routine
unit-test runs while making the release evidence reproducible.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta
from uuid import UUID

import pytest
import test_influencer_freshness_postgres as freshness_gate
import test_refresh_queue_postgres as queue_gate
import test_unified_preview_postgres as preview_gate
from backend_core.imports.enums import ImportMatchType, ImportRowAction
from backend_core.imports.models import ImportRow
from backend_core.influencers.enums import CRMStage, DataSource, Platform
from backend_core.influencers.freshness import FreshnessPolicy, FreshnessStatus
from backend_core.influencers.models import (
    Influencer,
    InfluencerCurrentMetrics,
    InfluencerPlatformAccount,
)
from backend_core.influencers.repository import InfluencerRepository
from backend_core.influencers.schemas import InfluencerListQuery
from backend_core.refresh.repository import RefreshQueueRepository
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

RELEASE_GATE_ENV = "RUN_PHASE2_RELEASE_GATE_TESTS"
INFLUENCER_COUNT = 2_000
ACCOUNTS_PER_INFLUENCER = 3
HISTORY_PER_ACCOUNT = 8
ACCOUNT_COUNT = INFLUENCER_COUNT * ACCOUNTS_PER_INFLUENCER
LINEAGE_ROW_COUNT = ACCOUNT_COUNT * HISTORY_PER_ACCOUNT


def _account_id(influencer_index: int, account_slot: int) -> UUID:
    return UUID(int=100_000 + (influencer_index * ACCOUNTS_PER_INFLUENCER) + account_slot + 1)


def _row_id(account_slot: int, history_index: int, influencer_index: int) -> UUID:
    ordinal = (
        (account_slot * HISTORY_PER_ACCOUNT * INFLUENCER_COUNT)
        + (history_index * INFLUENCER_COUNT)
        + influencer_index
        + 1
    )
    return UUID(int=1_000_000 + ordinal)


def _metric_hash(influencer_index: int, account_slot: int) -> str:
    ordinal = (influencer_index * ACCOUNTS_PER_INFLUENCER) + account_slot + 1
    return f"{20_000_000 + ordinal:064x}"


async def _seed_freshness_fanout(session: AsyncSession) -> None:
    primary, secondary = await freshness_gate._seed_operators(session)
    shared_created_at = freshness_gate.AS_OF - timedelta(days=365)
    latest_ages = (1, 60, 120)
    bundles = [
        [
            await freshness_gate._new_lineage_bundle(
                session,
                primary,
                acquired_at=freshness_gate.AS_OF - timedelta(days=latest_age + history_index),
            )
            for history_index in range(HISTORY_PER_ACCOUNT)
        ]
        for latest_age in latest_ages
    ]

    influencers = [
        Influencer(
            id=UUID(int=index + 1),
            display_name=f"Task 11 fanout {index:05d}",
            owner_operator_id=(primary if index % 2 == 0 else secondary).id,
            crm_stage=CRMStage.TO_DEVELOP,
            created_at=shared_created_at,
            updated_at=shared_created_at,
        )
        for index in range(INFLUENCER_COUNT)
    ]
    session.add_all(influencers)
    await session.flush()

    accounts = [
        InfluencerPlatformAccount(
            id=_account_id(influencer_index, account_slot),
            influencer_id=UUID(int=influencer_index + 1),
            platform=Platform.XIAOHONGSHU,
            platform_account_id=f"task11-{influencer_index:05d}-{account_slot}",
            account_name=f"Task 11 account {influencer_index:05d}-{account_slot}",
            account_handle=f"task11-{influencer_index:05d}-{account_slot}",
            profile_url=(
                "https://example.invalid/task11/" f"{influencer_index:05d}/{account_slot}"
            ),
            normalized_profile_url=(
                "https://example.invalid/task11/" f"{influencer_index:05d}/{account_slot}"
            ),
            source=DataSource.HUITUN,
            is_active=True,
            source_tags=["task11-fanout", f"slot-{account_slot}"],
        )
        for influencer_index in range(INFLUENCER_COUNT)
        for account_slot in range(ACCOUNTS_PER_INFLUENCER)
    ]
    session.add_all(accounts)
    await session.flush()

    rows: list[ImportRow] = []
    for account_slot in range(ACCOUNTS_PER_INFLUENCER):
        for history_index in range(HISTORY_PER_ACCOUNT):
            bundle = bundles[account_slot][history_index]
            for influencer_index in range(INFLUENCER_COUNT):
                account_id = _account_id(influencer_index, account_slot)
                ordinal = len(rows) + 1
                rows.append(
                    ImportRow(
                        id=_row_id(account_slot, history_index, influencer_index),
                        import_job_id=bundle.job.id,
                        import_job_file_id=bundle.occurrence.id,
                        row_number=influencer_index + 2,
                        raw_data={"task11_fanout": ordinal},
                        normalized_data={"task11_fanout": ordinal},
                        matched_influencer_id=UUID(int=influencer_index + 1),
                        matched_platform_account_id=account_id,
                        match_type=ImportMatchType.PLATFORM_ACCOUNT_ID,
                        action=ImportRowAction.NO_CHANGE,
                        warnings=[],
                        errors=[],
                        preview_revision=1,
                        plan_hash=f"{ordinal:064x}",
                        committed_action=ImportRowAction.NO_CHANGE,
                        committed_at=freshness_gate.AS_OF - timedelta(minutes=history_index + 1),
                    )
                )
    session.add_all(rows)
    await session.flush()

    session.add_all(
        [
            InfluencerCurrentMetrics(
                id=UUID(
                    int=10_000_000 + (influencer_index * ACCOUNTS_PER_INFLUENCER) + account_slot + 1
                ),
                influencer_id=UUID(int=influencer_index + 1),
                platform_account_id=_account_id(influencer_index, account_slot),
                source=DataSource.HUITUN,
                source_updated_at=freshness_gate.AS_OF - timedelta(days=1),
                metrics={"followers_count": 10_000 + influencer_index},
                metrics_hash=_metric_hash(influencer_index, account_slot),
                last_import_job_id=bundles[account_slot][0].job.id,
                last_import_row_id=_row_id(account_slot, 0, influencer_index),
            )
            for influencer_index in range(INFLUENCER_COUNT)
            for account_slot in range(ACCOUNTS_PER_INFLUENCER)
        ]
    )
    await session.commit()


def _measurement_document(
    measurement: preview_gate.SqlMeasurement,
) -> dict[str, int | float]:
    return {
        "sql_total": measurement.total,
        "sql_select": measurement.selects,
        "sql_dml": measurement.dml,
        "wall_seconds": round(measurement.wall_seconds, 6),
        "rss_high_water_after_bytes": measurement.rss_high_water_after_bytes,
        "rss_high_water_delta_bytes": measurement.rss_high_water_delta_bytes,
    }


@pytest.mark.skipif(
    os.environ.get(RELEASE_GATE_ENV) != "1",
    reason=f"set {RELEASE_GATE_ENV}=1 to run the Task 11 fanout release gate",
)
def test_freshness_2000_multi_account_long_lineage_release_gate() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            async with harness.factory() as session:
                await _seed_freshness_fanout(session)
                assert await session.scalar(select(func.count()).select_from(Influencer)) == 2_000
                assert (
                    await session.scalar(
                        select(func.count()).select_from(InfluencerPlatformAccount)
                    )
                    == ACCOUNT_COUNT
                )
                assert (
                    await session.scalar(select(func.count()).select_from(ImportRow))
                    == LINEAGE_ROW_COUNT
                )

            async with harness.factory() as session:
                repository = InfluencerRepository(session)
                harness.probe.start()
                records, total = await repository.list_influencers(
                    InfluencerListQuery(page=1, page_size=100),
                    as_of=freshness_gate.AS_OF,
                    policy=freshness_gate.POLICY,
                )
                list_measurement = harness.probe.stop(rows=len(records))
                assert total == INFLUENCER_COUNT
                assert len(records) == 100
                assert all(len(record.platform_accounts) == 3 for record in records)
                assert all(len(record.huitun_freshness) == 3 for record in records)

                very_stale_at = freshness_gate.AS_OF - timedelta(days=120)
                filtered_query = InfluencerListQuery(
                    freshness_status=FreshnessStatus.VERY_STALE,
                    requires_refresh=True,
                    last_huitun_observed_after=very_stale_at,
                    last_huitun_observed_before=very_stale_at,
                    page=1,
                    page_size=100,
                )
                harness.probe.start()
                filtered, filtered_total = await repository.list_influencers(
                    filtered_query,
                    as_of=freshness_gate.AS_OF,
                    policy=freshness_gate.POLICY,
                )
                filter_measurement = harness.probe.stop(rows=len(filtered))
                assert filtered_total == INFLUENCER_COUNT
                assert len(filtered) == 100

                harness.probe.start()
                detail = await repository.get_influencer_detail(UUID(int=INFLUENCER_COUNT))
                detail_measurement = harness.probe.stop(rows=1 if detail is not None else 0)
                assert detail is not None
                assert len(detail.platform_accounts) == 3
                assert len(detail.current_metrics) == 3
                assert len(detail.huitun_freshness) == 3

                await freshness_gate._explain_production_filtered_count(
                    session,
                    repository,
                    filtered_query,
                    expected_rows=LINEAGE_ROW_COUNT,
                )

            measurements = {
                "list": list_measurement,
                "filter": filter_measurement,
                "detail": detail_measurement,
            }
            print(
                "TASK11_FRESHNESS_FANOUT_2000 "
                + json.dumps(
                    {
                        "influencers": INFLUENCER_COUNT,
                        "accounts": ACCOUNT_COUNT,
                        "history_per_account": HISTORY_PER_ACCOUNT,
                        "lineage_rows": LINEAGE_ROW_COUNT,
                        "phases": {
                            label: _measurement_document(measurement)
                            for label, measurement in measurements.items()
                        },
                        "rss_high_water_bytes": max(
                            measurement.rss_high_water_after_bytes
                            for measurement in measurements.values()
                        ),
                    },
                    sort_keys=True,
                )
            )

            assert list_measurement.total == filter_measurement.total == 6
            # The probe counts CTE statements in total but only statements
            # beginning with SELECT in the narrower selects bucket.
            assert list_measurement.selects == 5
            assert filter_measurement.selects == 3
            assert detail_measurement.total == 7
            assert detail_measurement.selects == 6
            assert all(measurement.dml == 0 for measurement in measurements.values())
            assert all(measurement.wall_seconds < 30 for measurement in measurements.values())
            assert (
                max(measurement.rss_high_water_after_bytes for measurement in measurements.values())
                < 900 * 1024**2
            )

    asyncio.run(scenario())


@pytest.mark.skipif(
    os.environ.get(RELEASE_GATE_ENV) != "1",
    reason=f"set {RELEASE_GATE_ENV}=1 to run the Task 11 capacity release gate",
)
def test_freshness_and_refresh_candidate_5000_capacity_gate() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            async with harness.factory() as session:
                await freshness_gate._seed_scale(session, size=5_000)

            async with harness.factory() as session:
                repository = InfluencerRepository(session)
                harness.probe.start()
                records, total = await repository.list_influencers(
                    InfluencerListQuery(
                        freshness_status=FreshnessStatus.STALE,
                        requires_refresh=True,
                        page=1,
                        page_size=100,
                    ),
                    as_of=freshness_gate.AS_OF,
                    policy=freshness_gate.POLICY,
                )
                freshness_measurement = harness.probe.stop(rows=len(records))
                assert total == 1_000
                assert len(records) == 100

        async with queue_gate._isolated_postgres() as harness:
            async with harness.factory() as session:
                actor = await queue_gate._seed_actor(session, label="task11-capacity")
                specs = [
                    queue_gate.CandidateSpec(
                        label=f"task11-capacity-{index:05d}",
                        observed_at=queue_gate.AS_OF - timedelta(days=120),
                        influencer_id=UUID(int=index + 1),
                        account_id=UUID(int=100_000 + index + 1),
                    )
                    for index in range(5_000)
                ]
                await queue_gate._seed_candidates(session, actor, specs)

                repository = RefreshQueueRepository(session)
                harness.probe.start()
                candidates = await repository.list_candidates(
                    department_id=actor.department_id,
                    as_of=queue_gate.AS_OF,
                    policy=FreshnessPolicy(),
                    limit=2_000,
                )
                candidate_measurement = harness.probe.stop()
                assert len(candidates) == 2_000
                assert [candidate.influencer_id for candidate in candidates] == [
                    UUID(int=index + 1) for index in range(2_000)
                ]

        print(
            "TASK11_CAPACITY_5000 "
            + json.dumps(
                {
                    "freshness": _measurement_document(freshness_measurement),
                    "refresh_candidate": {
                        "sql_total": candidate_measurement.total,
                        "sql_select": candidate_measurement.selects,
                        "sql_dml": candidate_measurement.dml,
                        "wall_seconds": round(candidate_measurement.wall_seconds, 6),
                        "rss_high_water_after_bytes": (
                            candidate_measurement.rss_high_water_after_bytes
                        ),
                        "rss_high_water_delta_bytes": (
                            candidate_measurement.rss_high_water_delta_bytes
                        ),
                    },
                    "source_rows": 5_000,
                    "selected_candidates": 2_000,
                },
                sort_keys=True,
            )
        )
        assert freshness_measurement.total == 6
        assert freshness_measurement.dml == 0
        assert freshness_measurement.wall_seconds < 30
        assert candidate_measurement.total == candidate_measurement.selects == 1
        assert candidate_measurement.dml == 0
        assert candidate_measurement.wall_seconds < 30
        assert (
            max(
                freshness_measurement.rss_high_water_after_bytes,
                candidate_measurement.rss_high_water_after_bytes,
            )
            < 900 * 1024**2
        )

    asyncio.run(scenario())
