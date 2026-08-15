"""Opt-in PostgreSQL 16 end-to-end 2,000-item Refresh Return gate.

The destructive-test guard and isolated-schema harness are inherited from the
Unified Preview PostgreSQL gate.  The benchmark uses only synthetic Huitun
records and measures the return upload/parse/Preview path, claimed Confirm with
Queue reconciliation, and the post-Confirm Queue/Freshness verification reads.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
import test_bulk_confirm_postgres as confirm_gate
import test_influencer_freshness_postgres as freshness_gate
import test_refresh_return_reconciliation_postgres as return_gate
import test_unified_preview_postgres as preview_gate
from backend_core.imports.enums import ImportRowAction
from backend_core.imports.models import ImportJob
from backend_core.influencers.enums import DataSource
from backend_core.influencers.models import (
    Influencer,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
)
from backend_core.influencers.repository import InfluencerRepository
from backend_core.influencers.schemas import InfluencerListQuery
from backend_core.refresh.enums import RefreshQueueItemStatus, RefreshQueueStatus
from backend_core.refresh.models import RefreshQueue, RefreshQueueItem
from backend_core.refresh.reconciliation import (
    RefreshReturnReason,
    RefreshReturnRowOutcome,
)
from backend_core.refresh.repository import RefreshQueueRepository
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

PERFORMANCE_ENV = "RUN_REFRESH_RETURN_PERFORMANCE_TESTS"
QUEUE_ITEM_COUNT = 2_000
EXTRA_OUTSIDE_QUEUE_ACCOUNTS = 1
CHANGED_COUNT = 800
NO_CHANGE_COUNT = 600
STALE_COUNT = 300
MISSING_COUNT = 300
RETURN_ROW_COUNT = 1_705
BASELINE = freshness_gate.AS_OF - timedelta(days=60)
FRESH_ACQUISITION = freshness_gate.AS_OF - timedelta(days=1)
BASELINE_SOURCE_UPDATED_AT = "2026-06-01 12:00:00"
FRESH_SOURCE_UPDATED_AT = "2026-08-12 12:00:00"


def _identity(index: int) -> str:
    return f"task9-return-scale-{index:04d}"


def _chunks[T](items: list[T], size: int) -> list[list[T]]:
    return [items[offset : offset + size] for offset in range(0, len(items), size)]


def _baseline_files() -> list[list[dict[str, str]]]:
    rows = [
        freshness_gate._huitun_row(
            _identity(index),
            source_updated_at=BASELINE_SOURCE_UPDATED_AT,
            followers="200",
            nonce=f"baseline-{index}",
        )
        for index in range(QUEUE_ITEM_COUNT + EXTRA_OUTSIDE_QUEUE_ACCOUNTS)
    ]
    return _chunks(rows, 500)


def _return_files() -> tuple[list[list[dict[str, str]]], tuple[Any, ...]]:
    fresh_rows: list[dict[str, str]] = []
    for index in range(CHANGED_COUNT):
        fresh_rows.append(
            freshness_gate._huitun_row(
                _identity(index),
                source_updated_at=FRESH_SOURCE_UPDATED_AT,
                followers="250",
                nonce=f"changed-{index}",
            )
        )
    for index in range(CHANGED_COUNT, CHANGED_COUNT + NO_CHANGE_COUNT):
        fresh_rows.append(
            freshness_gate._huitun_row(
                _identity(index),
                source_updated_at=BASELINE_SOURCE_UPDATED_AT,
                followers="200",
                nonce=f"no-change-{index}",
            )
        )

    # An exact duplicate of an already-present changed row must be a SKIP
    # non-owner and can never become a second Queue claimant.
    fresh_rows.append(
        {
            **freshness_gate._huitun_row(
                _identity(0),
                source_updated_at=FRESH_SOURCE_UPDATED_AT,
                followers="250",
                nonce="changed-duplicate-non-owner",
            )
        }
    )

    # Equal-time conflicting payloads deliberately make the whole identity
    # component MANUAL_REVIEW.  Hard Match therefore does not uniquely select
    # a PlatformAccount, and the Queue Item remains a missing/pending return.
    manual_identity = _identity(CHANGED_COUNT + NO_CHANGE_COUNT + STALE_COUNT)
    fresh_rows.extend(
        (
            freshness_gate._huitun_row(
                manual_identity,
                source_updated_at=FRESH_SOURCE_UPDATED_AT,
                followers="333",
                nonce="manual-conflict-a",
            ),
            freshness_gate._huitun_row(
                manual_identity,
                source_updated_at=FRESH_SOURCE_UPDATED_AT,
                followers="444",
                nonce="manual-conflict-b",
            ),
        )
    )

    error_row = freshness_gate._huitun_row(
        "must-not-identify-error-row",
        source_updated_at=FRESH_SOURCE_UPDATED_AT,
        followers="200",
        nonce="missing-hard-identity",
    )
    error_row["达人官方地址"] = ""
    error_row["小红书号"] = ""
    fresh_rows.append(error_row)

    # The account exists and Hard Matches, but is intentionally not in the
    # linked Queue.  Its ordinary no-change lineage must still commit.
    fresh_rows.append(
        freshness_gate._huitun_row(
            _identity(QUEUE_ITEM_COUNT),
            source_updated_at=BASELINE_SOURCE_UPDATED_AT,
            followers="200",
            nonce="outside-linked-queue",
        )
    )

    stale_rows = [
        freshness_gate._huitun_row(
            _identity(index),
            source_updated_at=BASELINE_SOURCE_UPDATED_AT,
            followers="200",
            nonce=f"old-file-replay-{index}",
        )
        for index in range(
            CHANGED_COUNT + NO_CHANGE_COUNT,
            CHANGED_COUNT + NO_CHANGE_COUNT + STALE_COUNT,
        )
    ]
    assert len(fresh_rows) + len(stale_rows) == RETURN_ROW_COUNT
    return [fresh_rows, stale_rows], (FRESH_ACQUISITION, BASELINE)


async def _create_scale_queue(
    session: AsyncSession,
    anchor: preview_gate.SeededBatch,
) -> tuple[UUID, dict[str, UUID], dict[str, UUID]]:
    accounts = list(
        await session.scalars(
            select(InfluencerPlatformAccount).where(
                InfluencerPlatformAccount.platform_account_id.in_(
                    [_identity(index) for index in range(QUEUE_ITEM_COUNT)]
                )
            )
        )
    )
    assert len(accounts) == QUEUE_ITEM_COUNT
    accounts_by_identity = {account.platform_account_id: account for account in accounts}
    queue = RefreshQueue(
        department_id=anchor.department_id,
        created_by_operator_id=anchor.operator_id,
        status=RefreshQueueStatus.OPEN,
        as_of=freshness_gate.AS_OF,
        requested_limit=QUEUE_ITEM_COUNT,
        today_total_limit=QUEUE_ITEM_COUNT,
        refresh_limit=QUEUE_ITEM_COUNT,
        policy_version=1,
        criteria_snapshot={"schema_version": 1, "fixture": "task9-2k-return"},
    )
    session.add(queue)
    await session.flush()
    items = [
        RefreshQueueItem(
            department_id=anchor.department_id,
            queue_id=queue.id,
            influencer_id=accounts_by_identity[_identity(index)].influencer_id,
            platform_account_id=accounts_by_identity[_identity(index)].id,
            source=DataSource.HUITUN,
            priority_tier=2,
            priority_reasons=["VERY_STALE"],
            identity_snapshot={
                "platform_account_id": accounts_by_identity[_identity(index)].platform_account_id,
                "profile_url": accounts_by_identity[_identity(index)].profile_url,
            },
            baseline_last_observed_at=BASELINE,
            baseline_source_updated_at=None,
            status=RefreshQueueItemStatus.PENDING,
        )
        for index in range(QUEUE_ITEM_COUNT)
    ]
    session.add_all(items)
    await session.flush()
    await session.commit()
    return (
        queue.id,
        {identity: account.id for identity, account in accounts_by_identity.items()},
        {
            accounts_by_identity[_identity(index)].platform_account_id: items[index].id
            for index in range(QUEUE_ITEM_COUNT)
        },
    )


def _snapshot_document(item: InfluencerMetricSnapshot) -> tuple[Any, ...]:
    return (
        item.source_updated_at,
        dict(item.metrics),
        item.metrics_hash,
        item.snapshot_key,
    )


async def _freshness_observation(
    session: AsyncSession,
    identity: str,
    account_id: UUID,
) -> Any:
    records, _ = await InfluencerRepository(session).list_influencers(
        InfluencerListQuery(q=identity),
        as_of=freshness_gate.AS_OF,
        policy=freshness_gate.POLICY,
    )
    return next(
        freshness
        for record in records
        for freshness in record.huitun_freshness
        if freshness.platform_account_id == account_id
    )


def _measurement_document(measurement: preview_gate.SqlMeasurement) -> dict[str, Any]:
    return {
        "sql_total": measurement.total,
        "sql_select": measurement.selects,
        "sql_dml": measurement.dml,
        "sql_advisory": measurement.advisory,
        "wall_seconds": round(measurement.wall_seconds, 6),
        "rss_high_water_before_bytes": measurement.rss_high_water_before_bytes,
        "rss_high_water_after_bytes": measurement.rss_high_water_after_bytes,
        "rss_high_water_delta_bytes": measurement.rss_high_water_delta_bytes,
    }


def _expected_refresh_summary() -> dict[str, int]:
    return {
        "schema_version": 1,
        "row_count": RETURN_ROW_COUNT,
        "queue_item_count": QUEUE_ITEM_COUNT,
        "matched_row_count": CHANGED_COUNT + NO_CHANGE_COUNT + STALE_COUNT,
        "outside_queue_row_count": 1,
        "pending_row_count": 4,
        "queue_items_with_return_count": CHANGED_COUNT + NO_CHANGE_COUNT + STALE_COUNT,
        "queue_items_without_return_count": MISSING_COUNT,
        "expected_fulfilled_changed_count": CHANGED_COUNT,
        "expected_fulfilled_no_change_count": NO_CHANGE_COUNT,
        "expected_stale_return_count": STALE_COUNT,
        "expected_unresolved_count": 0,
        "unchanged_terminal_item_count": 0,
        "conflict_item_count": 0,
    }


@pytest.mark.skipif(
    os.environ.get(PERFORMANCE_ENV) != "1",
    reason=f"set {PERFORMANCE_ENV}=1 to run the formal Task 9 2,000-item gate",
)
def test_2000_item_mixed_refresh_return_end_to_end_resource_gate() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            baseline = await freshness_gate._confirm_huitun_batch(
                harness,
                _baseline_files(),
                acquired_at=tuple(BASELINE for _ in _baseline_files()),
            )
            async with harness.factory() as session:
                baseline_job = await session.get(ImportJob, baseline.job_id)
                baseline_rows = await preview_gate._job_rows(session, baseline.job_id)
                assert baseline_job is not None and baseline_job.preview_summary is not None
                assert "refresh_return" not in baseline_job.preview_summary
                assert all("refresh_return" not in row.merge_plan for row in baseline_rows)
                baseline_snapshots = {
                    item.id: _snapshot_document(item)
                    for item in await session.scalars(select(InfluencerMetricSnapshot))
                }
                assert len(baseline_snapshots) == QUEUE_ITEM_COUNT + 1
                queue_id, account_ids, queue_item_ids = await _create_scale_queue(
                    session,
                    baseline,
                )

            return_files, acquisition_times = _return_files()
            harness.probe.start()
            linked = await return_gate._new_linked_huitun_batch(
                harness,
                baseline,
                return_files,
                acquired_at=acquisition_times,
                refresh_queue_id=queue_id,
            )
            await preview_gate._parse_all(harness, linked)
            revision = await confirm_gate._preview(harness, linked)
            preview_measurement = harness.probe.stop(rows=RETURN_ROW_COUNT)

            async with harness.factory() as session:
                job = await session.get(ImportJob, linked.job_id)
                rows = await preview_gate._job_rows(session, linked.job_id)
                assert job is not None and job.preview_summary is not None
                assert len(rows) == RETURN_ROW_COUNT
                refresh_summary = dict(job.preview_summary["refresh_return"])
                missing_queue_item_ids = refresh_summary.pop("missing_queue_item_ids")
                assert refresh_summary == _expected_refresh_summary()
                expected_missing_ids = {
                    str(queue_item_ids[_identity(index)])
                    for index in range(
                        CHANGED_COUNT + NO_CHANGE_COUNT + STALE_COUNT,
                        QUEUE_ITEM_COUNT,
                    )
                }
                assert len(missing_queue_item_ids) == MISSING_COUNT
                assert set(missing_queue_item_ids) == expected_missing_ids
                assert len(missing_queue_item_ids) == len(set(missing_queue_item_ids))
                action_counts = Counter(row.action for row in rows)
                assert action_counts == {
                    ImportRowAction.UPDATE: CHANGED_COUNT,
                    ImportRowAction.NO_CHANGE: NO_CHANGE_COUNT + STALE_COUNT + 1,
                    ImportRowAction.SKIP: 1,
                    ImportRowAction.ERROR: 1,
                    ImportRowAction.MANUAL_REVIEW: 2,
                }
                row_evidence = [row.merge_plan["refresh_return"] for row in rows]
                assert Counter(item["outcome"] for item in row_evidence) == {
                    RefreshReturnRowOutcome.EXPECTED_FULFILLMENT.value: (
                        CHANGED_COUNT + NO_CHANGE_COUNT + STALE_COUNT
                    ),
                    RefreshReturnRowOutcome.OUTSIDE_QUEUE.value: 1,
                    RefreshReturnRowOutcome.PENDING.value: 4,
                }
                assert Counter(
                    item["reason"]
                    for item in row_evidence
                    if item["outcome"] == RefreshReturnRowOutcome.PENDING.value
                ) == {
                    RefreshReturnReason.ROW_ACTION_STAYS_PENDING.value: 1,
                    RefreshReturnReason.ROW_NOT_UNIQUELY_MATCHED.value: 3,
                }
                queue_statuses = Counter(
                    await session.scalars(
                        select(RefreshQueueItem.status).where(RefreshQueueItem.queue_id == queue_id)
                    )
                )
                assert queue_statuses == {RefreshQueueItemStatus.PENDING: QUEUE_ITEM_COUNT}
                assert all(
                    item.fulfilled_import_job_id is None
                    and item.fulfilled_import_row_id is None
                    and item.last_return_import_job_id is None
                    and item.last_return_import_row_id is None
                    for item in await session.scalars(
                        select(RefreshQueueItem).where(RefreshQueueItem.queue_id == queue_id)
                    )
                )

            harness.probe.start()
            await confirm_gate._request_confirm(harness, linked, revision)
            token, generation = await confirm_gate._claim_confirm(
                harness,
                linked.job_id,
                revision,
            )
            async with harness.factory() as session:
                result = await return_gate._processor(session, harness).confirm(
                    linked.job_id,
                    revision,
                    token,
                    generation,
                )
            confirm_measurement = harness.probe.stop(rows=RETURN_ROW_COUNT)
            assert {
                key: result[key]
                for key in (
                    "updated_rows",
                    "no_change_rows",
                    "skipped_rows",
                    "error_rows",
                    "manual_review_rows",
                )
            } == {
                "updated_rows": CHANGED_COUNT,
                "no_change_rows": NO_CHANGE_COUNT + STALE_COUNT + 1,
                "skipped_rows": 1,
                "error_rows": 1,
                "manual_review_rows": 2,
            }
            assert result["refresh_return"] == {
                **_expected_refresh_summary(),
                "claimed_item_count": CHANGED_COUNT + NO_CHANGE_COUNT + STALE_COUNT,
                "queue_completed": False,
            }

            harness.probe.start()
            async with harness.factory() as session:
                queue = await session.get(RefreshQueue, queue_id)
                items = list(
                    await session.scalars(
                        select(RefreshQueueItem)
                        .where(RefreshQueueItem.queue_id == queue_id)
                        .order_by(RefreshQueueItem.id)
                    )
                )
                rows = await preview_gate._job_rows(session, linked.job_id)
                assert queue is not None and queue.status is RefreshQueueStatus.OPEN
                assert queue.completed_at is None
                queue_counts = Counter(item.status.value for item in items)
                assert queue_counts == {
                    RefreshQueueItemStatus.FULFILLED_CHANGED.value: CHANGED_COUNT,
                    RefreshQueueItemStatus.FULFILLED_NO_CHANGE.value: NO_CHANGE_COUNT,
                    RefreshQueueItemStatus.STALE_RETURN.value: STALE_COUNT,
                    RefreshQueueItemStatus.PENDING.value: MISSING_COUNT,
                }
                summary = await RefreshQueueRepository(session).summary(queue_id)
                assert summary.status_breakdown == dict(sorted(queue_counts.items()))

                fulfilled = [
                    item
                    for item in items
                    if item.status
                    in {
                        RefreshQueueItemStatus.FULFILLED_CHANGED,
                        RefreshQueueItemStatus.FULFILLED_NO_CHANGE,
                    }
                ]
                returned = [item for item in items if item.last_return_import_row_id is not None]
                assert len(fulfilled) == CHANGED_COUNT + NO_CHANGE_COUNT
                assert len(returned) == CHANGED_COUNT + NO_CHANGE_COUNT + STALE_COUNT
                assert len({item.fulfilled_import_row_id for item in fulfilled}) == len(fulfilled)
                assert len({item.last_return_import_row_id for item in returned}) == len(returned)
                assert all(item.fulfilled_import_job_id == linked.job_id for item in fulfilled)
                assert all(item.last_return_import_job_id == linked.job_id for item in returned)

                rows_by_identity: dict[str, list[Any]] = {}
                for row in rows:
                    identity_document = (row.normalized_data or {}).get("platform_identity", {})
                    identity = identity_document.get("platform_account_id")
                    if identity is not None:
                        rows_by_identity.setdefault(identity, []).append(row)
                duplicate_rows = rows_by_identity[_identity(0)]
                assert len(duplicate_rows) == 2
                duplicate = next(
                    row for row in duplicate_rows if row.action is ImportRowAction.SKIP
                )
                owner = next(row for row in duplicate_rows if row.action is ImportRowAction.UPDATE)
                duplicate_item = next(
                    item for item in items if item.id == queue_item_ids[_identity(0)]
                )
                assert duplicate_item.fulfilled_import_row_id == owner.id
                assert duplicate_item.last_return_import_row_id == owner.id
                assert duplicate.id not in {item.last_return_import_row_id for item in returned}

                expected_account_count = QUEUE_ITEM_COUNT + EXTRA_OUTSIDE_QUEUE_ACCOUNTS
                assert (
                    await session.scalar(select(func.count()).select_from(Influencer))
                    == expected_account_count
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(InfluencerPlatformAccount)
                    )
                    == expected_account_count
                )
                assert (
                    await session.scalar(select(func.count()).select_from(InfluencerCurrentMetrics))
                    == expected_account_count
                )
                metrics = {
                    item.platform_account_id: item
                    for item in await session.scalars(select(InfluencerCurrentMetrics))
                }
                assert metrics[account_ids[_identity(0)]].metrics["followers_count"] == 250
                assert (
                    metrics[account_ids[_identity(CHANGED_COUNT)]].metrics["followers_count"] == 200
                )
                assert (
                    metrics[account_ids[_identity(CHANGED_COUNT + NO_CHANGE_COUNT)]].metrics[
                        "followers_count"
                    ]
                    == 200
                )

                snapshots = {
                    item.id: _snapshot_document(item)
                    for item in await session.scalars(select(InfluencerMetricSnapshot))
                }
                assert len(snapshots) == expected_account_count + CHANGED_COUNT
                assert {
                    item_id: snapshots[item_id] for item_id in baseline_snapshots
                } == baseline_snapshots

                changed_freshness = await _freshness_observation(
                    session,
                    _identity(0),
                    account_ids[_identity(0)],
                )
                no_change_freshness = await _freshness_observation(
                    session,
                    _identity(CHANGED_COUNT),
                    account_ids[_identity(CHANGED_COUNT)],
                )
                stale_freshness = await _freshness_observation(
                    session,
                    _identity(CHANGED_COUNT + NO_CHANGE_COUNT),
                    account_ids[_identity(CHANGED_COUNT + NO_CHANGE_COUNT)],
                )
                missing_freshness = await _freshness_observation(
                    session,
                    _identity(QUEUE_ITEM_COUNT - 1),
                    account_ids[_identity(QUEUE_ITEM_COUNT - 1)],
                )
                outside_freshness = await _freshness_observation(
                    session,
                    _identity(QUEUE_ITEM_COUNT),
                    next(
                        account.id
                        for account in await session.scalars(
                            select(InfluencerPlatformAccount).where(
                                InfluencerPlatformAccount.platform_account_id
                                == _identity(QUEUE_ITEM_COUNT)
                            )
                        )
                    ),
                )
                assert changed_freshness.last_observed_at == FRESH_ACQUISITION
                assert no_change_freshness.last_observed_at == FRESH_ACQUISITION
                assert stale_freshness.last_observed_at == BASELINE
                assert missing_freshness.last_observed_at == BASELINE
                assert outside_freshness.last_observed_at == FRESH_ACQUISITION
            verification_measurement = harness.probe.stop(rows=QUEUE_ITEM_COUNT)

            measurements = {
                "upload_parse_preview": preview_measurement,
                "confirm_reconcile": confirm_measurement,
                "verify_queue_freshness": verification_measurement,
            }
            total_wall = sum(item.wall_seconds for item in measurements.values())
            total_sql = sum(item.total for item in measurements.values())
            peak_rss = max(item.rss_high_water_after_bytes for item in measurements.values())
            rss_before = min(item.rss_high_water_before_bytes for item in measurements.values())
            document = {
                "queue_items": QUEUE_ITEM_COUNT,
                "return_rows": RETURN_ROW_COUNT,
                "queue_counts": dict(sorted(queue_counts.items())),
                "wall_seconds": round(total_wall, 6),
                "sql_total": total_sql,
                # ru_maxrss is a process-lifetime high-water mark, not an
                # instantaneous resident-set sample.
                "rss_high_water_after_bytes": peak_rss,
                "rss_high_water_delta_bytes": max(0, peak_rss - rss_before),
                "phases": {
                    label: _measurement_document(measurement)
                    for label, measurement in measurements.items()
                },
            }
            print("TASK9_REFRESH_RETURN_2000 " + json.dumps(document, sort_keys=True))
            # This phase includes durable Confirm request + claim as well as
            # the atomic processor, so its bound is intentionally above the
            # processor-only Task 6 benchmark while still rejecting N+1 work.
            assert confirm_measurement.selects <= 120
            assert confirm_measurement.total <= 140
            assert total_sql <= 420
            assert total_wall <= 120
            assert peak_rss <= 900 * 1024**2

    asyncio.run(scenario())
