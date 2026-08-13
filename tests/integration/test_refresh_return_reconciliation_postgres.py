"""PostgreSQL gates for atomic Refresh Return reconciliation.

The module reuses the destructive-test safety harness from Unified Preview:
``TEST_DATABASE_URL`` must point at PostgreSQL 16 and name a database containing
``phase1b_test``.  Each scenario runs in a disposable schema with synthetic data.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
import test_bulk_confirm_postgres as confirm_gate
import test_influencer_freshness_postgres as freshness_gate
import test_unified_preview_postgres as preview_gate
from backend_core.imports.confirm_processor import BulkConfirmProcessor
from backend_core.imports.enums import (
    ImportJobFileStatus,
    ImportJobStatus,
    ImportRowAction,
    ImportSourceType,
    ImportTaskState,
    SourceAcquiredAtOrigin,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.models import ImportJob, ImportJobFile, ImportTaskRequest
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.task_service import ClaimStatus, ImportTaskService, TaskEnvelope
from backend_core.influencers.enums import DataSource
from backend_core.influencers.models import (
    InfluencerCurrentMetrics,
    InfluencerPlatformAccount,
)
from backend_core.influencers.repository import InfluencerRepository
from backend_core.influencers.schemas import InfluencerListQuery
from backend_core.refresh.enums import RefreshQueueItemStatus, RefreshQueueStatus
from backend_core.refresh.models import RefreshQueue, RefreshQueueItem
from backend_core.refresh.reconciliation import RefreshReturnReason, RefreshReturnRowOutcome
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

AS_OF = freshness_gate.AS_OF


async def _account_for_identity(
    session: AsyncSession,
    identity: str,
) -> InfluencerPlatformAccount:
    account = await session.scalar(
        select(InfluencerPlatformAccount).where(
            InfluencerPlatformAccount.normalized_profile_url
            == f"https://www.xiaohongshu.com/user/profile/{identity}"
        )
    )
    assert account is not None
    return account


async def _freshness_for_account(
    session: AsyncSession,
    identity: str,
    account_id: UUID,
) -> Any:
    records, _ = await InfluencerRepository(session).list_influencers(
        InfluencerListQuery(q=identity),
        as_of=AS_OF,
        policy=freshness_gate.POLICY,
    )
    return next(
        item
        for record in records
        for item in record.huitun_freshness
        if item.platform_account_id == account_id
    )


async def _new_linked_huitun_batch(
    harness: preview_gate.PostgresHarness,
    anchor: preview_gate.SeededBatch,
    files: Iterable[Iterable[dict[str, str]]],
    *,
    acquired_at: tuple[datetime, ...],
    refresh_queue_id: UUID,
    confirmation_required: tuple[bool, ...] | None = None,
) -> preview_gate.SeededBatch:
    """Create a distinct Bulk job under the anchor's department and collection."""

    rows_by_file = [list(rows) for rows in files]
    assert len(rows_by_file) == len(acquired_at)
    confirmation_flags = confirmation_required or tuple(False for _ in rows_by_file)
    assert len(rows_by_file) == len(confirmation_flags)
    async with harness.factory() as session:
        anchor_job = await session.get(ImportJob, anchor.job_id)
        assert anchor_job is not None
        job = ImportJob(
            collection_job_id=anchor_job.collection_job_id,
            refresh_queue_id=refresh_queue_id,
            department_id=anchor.department_id,
            operator_id=anchor.operator_id,
            source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
            status=ImportJobStatus.DRAFT,
            preview_revision=0,
        )
        session.add(job)
        await session.flush()

        occurrences: list[ImportJobFile] = []
        for position, (rows, observed_at, requires_confirmation) in enumerate(
            zip(rows_by_file, acquired_at, confirmation_flags, strict=True),
            start=1,
        ):
            stored = await preview_gate._store_csv(
                session,
                harness.storage,
                rows,
                headers=freshness_gate.HUITUN_HEADERS,
            )
            occurrence = ImportJobFile(
                import_job_id=job.id,
                stored_file_id=stored.id,
                position=position,
                original_filename=f"task9-return-{position}.csv",
                declared_mime="text/csv",
                status=ImportJobFileStatus.PARSING,
                source_acquired_at=observed_at,
                source_acquired_at_origin=(
                    SourceAcquiredAtOrigin.SERVER_DEFAULT
                    if requires_confirmation
                    else SourceAcquiredAtOrigin.USER_CONFIRMED
                ),
                source_acquired_at_confirmation_required=requires_confirmation,
                field_mapping=dict(freshness_gate.HUITUN_MAPPING),
                parse_task_id=f"task9-parse-{job.id}-{position}",
                parse_attempts=1,
                parse_started_at=datetime.now(UTC),
            )
            session.add(occurrence)
            occurrences.append(occurrence)
        await session.commit()
        return preview_gate.SeededBatch(
            job.id,
            anchor.department_id,
            anchor.operator_id,
            anchor.auth_session_id,
            tuple(occurrences),
        )


async def _create_queue(
    harness: preview_gate.PostgresHarness,
    anchor: preview_gate.SeededBatch,
    entries: Iterable[tuple[InfluencerPlatformAccount, datetime | None]],
) -> tuple[UUID, dict[UUID, UUID]]:
    candidates = list(entries)
    async with harness.factory() as session:
        queue = RefreshQueue(
            department_id=anchor.department_id,
            created_by_operator_id=anchor.operator_id,
            status=RefreshQueueStatus.OPEN,
            as_of=AS_OF,
            requested_limit=len(candidates),
            today_total_limit=2_000,
            refresh_limit=2_000,
            policy_version=1,
            criteria_snapshot={"schema_version": 1, "fixture": "task9-return"},
        )
        session.add(queue)
        await session.flush()
        item_ids: dict[UUID, UUID] = {}
        for account, baseline in candidates:
            item = RefreshQueueItem(
                department_id=anchor.department_id,
                queue_id=queue.id,
                influencer_id=account.influencer_id,
                platform_account_id=account.id,
                source=DataSource.HUITUN,
                priority_tier=2,
                priority_reasons=["VERY_STALE"],
                identity_snapshot={
                    "platform_account_id": account.platform_account_id,
                    "profile_url": account.profile_url,
                },
                baseline_last_observed_at=baseline,
                baseline_source_updated_at=None,
                status=RefreshQueueItemStatus.PENDING,
            )
            session.add(item)
            await session.flush()
            item_ids[account.id] = item.id
        await session.commit()
        return queue.id, item_ids


async def _prepare_claimed_confirm(
    harness: preview_gate.PostgresHarness,
    seeded: preview_gate.SeededBatch,
) -> tuple[int, UUID, int, TaskEnvelope]:
    await preview_gate._parse_all(harness, seeded)
    revision = await confirm_gate._preview(harness, seeded)
    await confirm_gate._request_confirm(harness, seeded, revision)
    token, generation = await confirm_gate._claim_confirm(harness, seeded.job_id, revision)
    async with harness.factory() as session:
        task = await session.scalar(
            select(ImportTaskRequest).where(ImportTaskRequest.task_token == token)
        )
        assert task is not None
        envelope = TaskEnvelope.from_task(task)
    return revision, token, generation, envelope


def _processor(
    session: AsyncSession,
    harness: preview_gate.PostgresHarness,
) -> BulkConfirmProcessor:
    return BulkConfirmProcessor(
        session,
        harness.storage,
        parser_limits=ParserLimits(
            max_rows=10_000,
            max_columns=100,
            max_cells=1_000_000,
        ),
        settings=confirm_gate._settings(),
        max_batch_rows=10_000,
    )


async def _queue_snapshot(
    session: AsyncSession,
    queue_id: UUID,
) -> tuple[Any, ...]:
    queue = await session.get(RefreshQueue, queue_id)
    items = list(
        await session.scalars(
            select(RefreshQueueItem)
            .where(RefreshQueueItem.queue_id == queue_id)
            .order_by(RefreshQueueItem.id)
        )
    )
    assert queue is not None
    return (
        queue.status,
        queue.completed_at,
        tuple(
            (
                item.id,
                item.status,
                item.fulfilled_import_job_id,
                item.fulfilled_import_row_id,
                item.fulfilled_at,
                item.last_return_import_job_id,
                item.last_return_import_row_id,
            )
            for item in items
        ),
    )


async def _assert_ordinary_preview_has_no_refresh_evidence(
    session: AsyncSession,
    job_id: UUID,
) -> None:
    job = await session.get(ImportJob, job_id)
    rows = await preview_gate._job_rows(session, job_id)
    assert job is not None and job.preview_summary is not None
    assert "refresh_return" not in job.preview_summary
    assert all("refresh_return" not in row.merge_plan for row in rows)


async def _assert_linked_preview_evidence(
    session: AsyncSession,
    job_id: UUID,
    expected_by_account: dict[
        UUID,
        tuple[UUID, RefreshQueueItemStatus, RefreshReturnReason],
    ],
) -> None:
    job = await session.get(ImportJob, job_id)
    rows = await preview_gate._job_rows(session, job_id)
    files = list(
        await session.scalars(
            select(ImportJobFile)
            .where(ImportJobFile.import_job_id == job_id)
            .order_by(ImportJobFile.position)
        )
    )
    file_positions = {file.id: file.position for file in files}
    assert job is not None and job.preview_summary is not None
    statuses = Counter(expected_status for _, expected_status, _ in expected_by_account.values())
    assert job.preview_summary["refresh_return"] == {
        "schema_version": 1,
        "row_count": len(rows),
        "queue_item_count": len(expected_by_account),
        "matched_row_count": len(expected_by_account),
        "outside_queue_row_count": 0,
        "pending_row_count": 0,
        "queue_items_with_return_count": len(expected_by_account),
        "queue_items_without_return_count": 0,
        "expected_fulfilled_changed_count": statuses[RefreshQueueItemStatus.FULFILLED_CHANGED],
        "expected_fulfilled_no_change_count": statuses[RefreshQueueItemStatus.FULFILLED_NO_CHANGE],
        "expected_stale_return_count": statuses[RefreshQueueItemStatus.STALE_RETURN],
        "expected_unresolved_count": statuses[RefreshQueueItemStatus.UNRESOLVED],
        "unchanged_terminal_item_count": 0,
        "conflict_item_count": 0,
        "missing_queue_item_ids": [],
    }
    assert {
        row.matched_platform_account_id
        for row in rows
        if row.matched_platform_account_id is not None
    } == set(expected_by_account)
    for row in rows:
        assert row.matched_platform_account_id is not None
        queue_item_id, expected_status, reason = expected_by_account[
            row.matched_platform_account_id
        ]
        assert row.merge_plan["refresh_return"] == {
            "locator": {
                "import_job_file_id": str(row.import_job_file_id),
                "file_position": file_positions[row.import_job_file_id],
                "row_number": row.row_number,
                "import_row_id": str(row.id),
            },
            "outcome": RefreshReturnRowOutcome.EXPECTED_FULFILLMENT.value,
            "queue_item_id": str(queue_item_id),
            "expected_status": expected_status.value,
            "reason": reason.value,
            "is_last_return_claimant": True,
        }


def test_refresh_fulfillment_rolls_back_with_confirm_then_replays_once() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            identity = "task9-atomic-account"
            baseline = AS_OF - timedelta(days=60)
            initial = await freshness_gate._confirm_huitun_batch(
                harness,
                [[freshness_gate._huitun_row(identity, nonce="atomic-baseline")]],
                acquired_at=(baseline,),
            )
            async with harness.factory() as session:
                account = await _account_for_identity(session, identity)
                account_id = account.id
                account_for_queue = account
                await _assert_ordinary_preview_has_no_refresh_evidence(session, initial.job_id)
            queue_id, item_ids = await _create_queue(
                harness,
                initial,
                [(account_for_queue, baseline)],
            )
            linked = await _new_linked_huitun_batch(
                harness,
                initial,
                [
                    [
                        freshness_gate._huitun_row(
                            identity,
                            source_updated_at="2026-08-12 12:00:00",
                            followers="250",
                            nonce="atomic-return",
                        )
                    ]
                ],
                acquired_at=(AS_OF - timedelta(days=1),),
                refresh_queue_id=queue_id,
            )
            revision, token, generation, envelope = await _prepare_claimed_confirm(
                harness,
                linked,
            )
            async with harness.factory() as session:
                preview_rows = await preview_gate._job_rows(session, linked.job_id)
                assert len(preview_rows) == 1
                assert preview_rows[0].action is ImportRowAction.UPDATE
                assert preview_rows[0].matched_platform_account_id == account_id
                await _assert_linked_preview_evidence(
                    session,
                    linked.job_id,
                    {
                        account_id: (
                            item_ids[account_id],
                            RefreshQueueItemStatus.FULFILLED_CHANGED,
                            RefreshReturnReason.EFFECTIVE_CHANGES,
                        )
                    },
                )

            def fail_before_commit(_session: object) -> None:
                raise RuntimeError("synthetic final commit failure")

            async with harness.factory() as session:
                event.listen(session.sync_session, "before_commit", fail_before_commit, once=True)
                with pytest.raises(ImportDomainError) as failed:
                    await _processor(session, harness).confirm(
                        linked.job_id,
                        revision,
                        token,
                        generation,
                    )
                assert failed.value.code == "IMPORT_CONFIRM_FAILED"

            async with harness.factory() as session:
                job = await session.get(ImportJob, linked.job_id)
                item = await session.get(RefreshQueueItem, item_ids[account_id])
                task = await session.scalar(
                    select(ImportTaskRequest).where(ImportTaskRequest.task_token == token)
                )
                row = (await preview_gate._job_rows(session, linked.job_id))[0]
                current = await session.scalar(
                    select(InfluencerCurrentMetrics).where(
                        InfluencerCurrentMetrics.platform_account_id == account_id,
                        InfluencerCurrentMetrics.source == DataSource.HUITUN,
                    )
                )
                assert job is not None and job.status is ImportJobStatus.CONFIRM_QUEUED
                assert item is not None and item.status is RefreshQueueItemStatus.PENDING
                assert item.fulfilled_import_job_id is None
                assert item.fulfilled_import_row_id is None
                assert item.fulfilled_at is None
                assert row.committed_at is None
                assert row.committed_action is None
                assert task is not None and task.state is ImportTaskState.RUNNING
                assert current is not None and current.metrics["followers_count"] == 200

            async with harness.factory() as session:
                result = await _processor(session, harness).confirm(
                    linked.job_id,
                    revision,
                    token,
                    generation,
                )
                assert result["updated_rows"] == 1
                assert result["refresh_return"]["claimed_item_count"] == 1
                assert result["refresh_return"]["queue_completed"] is True

            async with harness.factory() as session:
                item = await session.get(RefreshQueueItem, item_ids[account_id])
                queue = await session.get(RefreshQueue, queue_id)
                row = (await preview_gate._job_rows(session, linked.job_id))[0]
                task = await session.scalar(
                    select(ImportTaskRequest).where(ImportTaskRequest.task_token == token)
                )
                assert item is not None
                assert item.status is RefreshQueueItemStatus.FULFILLED_CHANGED
                assert item.fulfilled_import_job_id == linked.job_id
                assert item.fulfilled_import_row_id == row.id
                assert item.fulfilled_at is not None
                assert item.last_return_import_job_id == linked.job_id
                assert item.last_return_import_row_id == row.id
                assert queue is not None and queue.status is RefreshQueueStatus.COMPLETED
                assert queue.completed_at is not None
                assert row.committed_action is ImportRowAction.UPDATE
                assert row.committed_at is not None
                assert task is not None and task.state is ImportTaskState.COMPLETED
                before_replay = await _queue_snapshot(session, queue_id)

            async with harness.factory() as session:
                replay = await ImportTaskService(
                    session,
                    confirm_gate._settings(),
                ).claim(envelope)
                assert replay.status is ClaimStatus.COMPLETED
                assert replay.generation is None
                await session.commit()
            async with harness.factory() as session:
                assert await _queue_snapshot(session, queue_id) == before_replay

    asyncio.run(scenario())


def test_two_linked_confirms_have_exactly_one_queue_item_winner() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            identity = "task9-concurrent-account"
            baseline = AS_OF - timedelta(days=60)
            initial = await freshness_gate._confirm_huitun_batch(
                harness,
                [[freshness_gate._huitun_row(identity, nonce="concurrent-baseline")]],
                acquired_at=(baseline,),
            )
            async with harness.factory() as session:
                account = await _account_for_identity(session, identity)
                account_id = account.id
                account_for_queue = account
            queue_id, item_ids = await _create_queue(
                harness,
                initial,
                [(account_for_queue, baseline)],
            )

            async def make_return(nonce: str) -> preview_gate.SeededBatch:
                return await _new_linked_huitun_batch(
                    harness,
                    initial,
                    [
                        [
                            freshness_gate._huitun_row(
                                identity,
                                source_updated_at="2026-08-12 12:00:00",
                                followers="275",
                                nonce=nonce,
                            )
                        ]
                    ],
                    acquired_at=(AS_OF - timedelta(days=1),),
                    refresh_queue_id=queue_id,
                )

            first = await make_return("concurrent-return-a")
            second = await make_return("concurrent-return-b")
            first_claim = await _prepare_claimed_confirm(harness, first)
            second_claim = await _prepare_claimed_confirm(harness, second)
            async with harness.factory() as session:
                expected = {
                    account_id: (
                        item_ids[account_id],
                        RefreshQueueItemStatus.FULFILLED_CHANGED,
                        RefreshReturnReason.EFFECTIVE_CHANGES,
                    )
                }
                await _assert_linked_preview_evidence(session, first.job_id, expected)
                await _assert_linked_preview_evidence(session, second.job_id, expected)
            barrier = asyncio.Barrier(2)
            results = await asyncio.gather(
                confirm_gate._run_claimed_confirm(
                    harness,
                    first,
                    first_claim[0],
                    first_claim[1],
                    first_claim[2],
                    barrier,
                ),
                confirm_gate._run_claimed_confirm(
                    harness,
                    second,
                    second_claim[0],
                    second_claim[1],
                    second_claim[2],
                    barrier,
                ),
            )
            assert sum("updated_rows" in result for result in results) == 1
            winner_result = next(result for result in results if "updated_rows" in result)
            assert winner_result["refresh_return"]["claimed_item_count"] == 1
            assert winner_result["refresh_return"]["queue_completed"] is True
            assert (
                sum(
                    result.get("status") == ImportJobStatus.PREVIEW_STALE.value
                    for result in results
                )
                == 1
            )

            async with harness.factory() as session:
                item = await session.get(RefreshQueueItem, item_ids[account_id])
                queue = await session.get(RefreshQueue, queue_id)
                jobs = [
                    await session.get(ImportJob, first.job_id),
                    await session.get(ImportJob, second.job_id),
                ]
                assert item is not None
                assert item.status is RefreshQueueItemStatus.FULFILLED_CHANGED
                assert item.fulfilled_import_job_id in {first.job_id, second.job_id}
                winner = next(
                    job
                    for job in jobs
                    if job is not None and job.id == item.fulfilled_import_job_id
                )
                loser = next(
                    job
                    for job in jobs
                    if job is not None and job.id != item.fulfilled_import_job_id
                )
                assert winner.status is ImportJobStatus.COMPLETED
                assert loser.status is ImportJobStatus.PREVIEW_STALE
                winner_row = (await preview_gate._job_rows(session, winner.id))[0]
                loser_row = (await preview_gate._job_rows(session, loser.id))[0]
                assert item.fulfilled_import_row_id == winner_row.id
                assert winner_row.committed_at is not None
                assert loser_row.committed_at is None
                assert queue is not None and queue.status is RefreshQueueStatus.COMPLETED
                assert queue.completed_at is not None

    asyncio.run(scenario())


def test_no_change_advances_freshness_but_stale_return_does_not() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            advancing_identity = "task9-freshness-advance"
            stale_identity = "task9-freshness-stale"
            advancing_baseline = AS_OF - timedelta(days=60)
            stale_baseline = AS_OF - timedelta(days=15)
            initial = await freshness_gate._confirm_huitun_batch(
                harness,
                [
                    [
                        freshness_gate._huitun_row(
                            advancing_identity,
                            followers="200",
                            nonce="freshness-advance-baseline",
                        )
                    ],
                    [
                        freshness_gate._huitun_row(
                            stale_identity,
                            followers="300",
                            nonce="freshness-stale-baseline",
                        )
                    ],
                ],
                acquired_at=(advancing_baseline, stale_baseline),
            )
            async with harness.factory() as session:
                advancing_account = await _account_for_identity(session, advancing_identity)
                stale_account = await _account_for_identity(session, stale_identity)
                advancing_account_id = advancing_account.id
                stale_account_id = stale_account.id
                accounts_for_queue = (advancing_account, stale_account)
            queue_id, item_ids = await _create_queue(
                harness,
                initial,
                [
                    (accounts_for_queue[0], advancing_baseline),
                    (accounts_for_queue[1], stale_baseline),
                ],
            )
            advancing_observed = AS_OF - timedelta(days=1)
            stale_observed = AS_OF - timedelta(days=30)
            linked = await _new_linked_huitun_batch(
                harness,
                initial,
                [
                    [
                        freshness_gate._huitun_row(
                            advancing_identity,
                            followers="200",
                            nonce="freshness-advance-return",
                        )
                    ],
                    [
                        freshness_gate._huitun_row(
                            stale_identity,
                            followers="300",
                            nonce="freshness-stale-return",
                        )
                    ],
                ],
                acquired_at=(advancing_observed, stale_observed),
                refresh_queue_id=queue_id,
            )
            revision, token, generation, _ = await _prepare_claimed_confirm(harness, linked)
            async with harness.factory() as session:
                rows = await preview_gate._job_rows(session, linked.job_id)
                assert [row.action for row in rows] == [
                    ImportRowAction.NO_CHANGE,
                    ImportRowAction.NO_CHANGE,
                ]
                await _assert_linked_preview_evidence(
                    session,
                    linked.job_id,
                    {
                        advancing_account_id: (
                            item_ids[advancing_account_id],
                            RefreshQueueItemStatus.FULFILLED_NO_CHANGE,
                            RefreshReturnReason.RELIABLE_NO_CHANGE,
                        ),
                        stale_account_id: (
                            item_ids[stale_account_id],
                            RefreshQueueItemStatus.STALE_RETURN,
                            RefreshReturnReason.ACQUISITION_NOT_NEWER_THAN_BASELINE,
                        ),
                    },
                )
            async with harness.factory() as session:
                result = await _processor(session, harness).confirm(
                    linked.job_id,
                    revision,
                    token,
                    generation,
                )
                assert result["no_change_rows"] == 2
                assert result["refresh_return"]["claimed_item_count"] == 2
                assert result["refresh_return"]["queue_completed"] is False

            async with harness.factory() as session:
                rows = await preview_gate._job_rows(session, linked.job_id)
                rows_by_account = {
                    row.matched_platform_account_id: row
                    for row in rows
                    if row.matched_platform_account_id is not None
                }
                advancing_item = await session.get(
                    RefreshQueueItem,
                    item_ids[advancing_account_id],
                )
                stale_item = await session.get(
                    RefreshQueueItem,
                    item_ids[stale_account_id],
                )
                queue = await session.get(RefreshQueue, queue_id)
                assert advancing_item is not None
                assert advancing_item.status is RefreshQueueItemStatus.FULFILLED_NO_CHANGE
                assert advancing_item.fulfilled_import_job_id == linked.job_id
                assert (
                    advancing_item.fulfilled_import_row_id
                    == rows_by_account[advancing_account_id].id
                )
                assert stale_item is not None
                assert stale_item.status is RefreshQueueItemStatus.STALE_RETURN
                assert stale_item.fulfilled_import_job_id is None
                assert stale_item.fulfilled_import_row_id is None
                assert stale_item.fulfilled_at is None
                assert stale_item.last_return_import_job_id == linked.job_id
                assert stale_item.last_return_import_row_id == rows_by_account[stale_account_id].id
                assert queue is not None and queue.status is RefreshQueueStatus.OPEN
                assert queue.completed_at is None

                records, _ = await InfluencerRepository(session).list_influencers(
                    InfluencerListQuery(q="task9-freshness-"),
                    as_of=AS_OF,
                    policy=freshness_gate.POLICY,
                )
                by_account = {
                    item.platform_account_id: item
                    for record in records
                    for item in record.huitun_freshness
                }
                assert by_account[advancing_account_id].last_observed_at == advancing_observed
                assert by_account[stale_account_id].last_observed_at == stale_baseline

    asyncio.run(scenario())


def test_confirmation_required_linked_return_merges_but_does_not_fulfill() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            identity = "task9-confirmation-required"
            baseline = AS_OF - timedelta(days=60)
            initial = await freshness_gate._confirm_huitun_batch(
                harness,
                [[freshness_gate._huitun_row(identity, nonce="confirmation-baseline")]],
                acquired_at=(baseline,),
            )
            async with harness.factory() as session:
                account = await _account_for_identity(session, identity)
                account_id = account.id
                account_for_queue = account
            queue_id, item_ids = await _create_queue(
                harness,
                initial,
                [(account_for_queue, baseline)],
            )
            linked = await _new_linked_huitun_batch(
                harness,
                initial,
                [
                    [
                        freshness_gate._huitun_row(
                            identity,
                            source_updated_at="2026-08-12 12:00:00",
                            followers="999",
                            nonce="confirmation-required-return",
                        )
                    ]
                ],
                acquired_at=(AS_OF - timedelta(days=1),),
                refresh_queue_id=queue_id,
                confirmation_required=(True,),
            )
            revision, token, generation, _ = await _prepare_claimed_confirm(harness, linked)
            async with harness.factory() as session:
                await _assert_linked_preview_evidence(
                    session,
                    linked.job_id,
                    {
                        account_id: (
                            item_ids[account_id],
                            RefreshQueueItemStatus.UNRESOLVED,
                            RefreshReturnReason.ACQUISITION_CONFIRMATION_REQUIRED,
                        )
                    },
                )
            async with harness.factory() as session:
                result = await _processor(session, harness).confirm(
                    linked.job_id,
                    revision,
                    token,
                    generation,
                )
                assert result["updated_rows"] == 1
                assert result["refresh_return"]["queue_completed"] is False

            async with harness.factory() as session:
                item = await session.get(RefreshQueueItem, item_ids[account_id])
                row = (await preview_gate._job_rows(session, linked.job_id))[0]
                current = await session.scalar(
                    select(InfluencerCurrentMetrics).where(
                        InfluencerCurrentMetrics.platform_account_id == account_id,
                        InfluencerCurrentMetrics.source == DataSource.HUITUN,
                    )
                )
                assert item is not None and item.status is RefreshQueueItemStatus.UNRESOLVED
                assert item.last_return_import_job_id == linked.job_id
                assert item.last_return_import_row_id == row.id
                assert item.fulfilled_import_job_id is None
                assert current is not None and current.metrics["followers_count"] == 999
                freshness = await _freshness_for_account(session, identity, account_id)
                assert freshness.last_observed_at == baseline

    asyncio.run(scenario())
