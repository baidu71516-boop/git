"""Pure and projection-level coverage for Refresh return reconciliation."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.enums import ImportRowAction
from backend_core.imports.preview_domain import (
    ChangeEffect,
    ChangeScope,
    ChangeSummary,
    FieldChange,
    PreviewRowLocator,
)
from backend_core.influencers.enums import DataSource
from backend_core.refresh.enums import RefreshQueueItemStatus, RefreshQueueStatus
from backend_core.refresh.models import RefreshQueue, RefreshQueueItem
from backend_core.refresh.reconciliation import (
    RefreshReturnQueueItem,
    RefreshReturnReason,
    RefreshReturnRow,
    RefreshReturnRowOutcome,
    reconcile_refresh_return,
)
from backend_core.refresh.repository import RefreshQueueRepository
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def _locator(*, position: int = 1, row_number: int = 2) -> PreviewRowLocator:
    return PreviewRowLocator(
        import_job_file_id=uuid4(),
        file_position=position,
        row_number=row_number,
        import_row_id=uuid4(),
    )


def _changed_summary() -> ChangeSummary:
    return ChangeSummary(
        effective_changes=(
            FieldChange(
                scope=ChangeScope.ACCOUNT,
                field="bio",
                before="before",
                incoming="after",
                after="after",
                effect=ChangeEffect.APPLY,
                reason="ACCOUNT_UPDATE_APPLIED",
            ),
        )
    )


def _row(
    account_id: UUID | None,
    *,
    action: ImportRowAction = ImportRowAction.NO_CHANGE,
    source: DataSource = DataSource.HUITUN,
    owner_effective: bool = True,
    acquisition: datetime | None = NOW,
    confirmation_required: bool = False,
    change_summary: ChangeSummary | None = None,
    locator: PreviewRowLocator | None = None,
) -> RefreshReturnRow:
    return RefreshReturnRow(
        locator=locator or _locator(),
        source=source,
        action=action,
        matched_platform_account_id=account_id,
        is_owner_effective=owner_effective,
        source_acquired_at=acquisition,
        source_acquired_at_confirmation_required=confirmation_required,
        change_summary=change_summary or ChangeSummary(),
    )


def _item(
    account_id: UUID,
    *,
    baseline: datetime | None = NOW - timedelta(days=1),
    status: RefreshQueueItemStatus = RefreshQueueItemStatus.PENDING,
    queue_id: UUID | None = None,
    item_id: UUID | None = None,
) -> RefreshReturnQueueItem:
    return RefreshReturnQueueItem(
        queue_id=queue_id or uuid4(),
        queue_item_id=item_id or uuid4(),
        platform_account_id=account_id,
        source=DataSource.HUITUN,
        baseline_last_observed_at=baseline,
        status=status,
    )


@pytest.mark.parametrize(
    ("baseline", "acquisition", "expected_status", "expected_reason"),
    [
        (
            None,
            NOW,
            RefreshQueueItemStatus.FULFILLED_CHANGED,
            RefreshReturnReason.EFFECTIVE_CHANGES,
        ),
        (
            NOW - timedelta(seconds=1),
            NOW,
            RefreshQueueItemStatus.FULFILLED_CHANGED,
            RefreshReturnReason.EFFECTIVE_CHANGES,
        ),
        (
            NOW,
            NOW,
            RefreshQueueItemStatus.STALE_RETURN,
            RefreshReturnReason.ACQUISITION_NOT_NEWER_THAN_BASELINE,
        ),
        (
            NOW + timedelta(seconds=1),
            NOW,
            RefreshQueueItemStatus.STALE_RETURN,
            RefreshReturnReason.ACQUISITION_NOT_NEWER_THAN_BASELINE,
        ),
    ],
)
def test_changed_uses_typed_effective_changes_and_strict_baseline_boundary(
    baseline: datetime | None,
    acquisition: datetime,
    expected_status: RefreshQueueItemStatus,
    expected_reason: RefreshReturnReason,
) -> None:
    account_id = uuid4()
    item = _item(account_id, baseline=baseline)
    row = _row(
        account_id,
        action=ImportRowAction.UPDATE,
        acquisition=acquisition,
        change_summary=_changed_summary(),
    )

    result = reconcile_refresh_return([row], [item])

    evidence = result.item_evidence[0]
    assert evidence.expected_status is expected_status
    assert evidence.reason is expected_reason
    assert evidence.claimant_locator == row.locator
    assert result.row_evidence[0].is_last_return_claimant is True


@pytest.mark.parametrize(
    (
        "baseline",
        "acquisition",
        "confirmation_required",
        "expected_status",
        "expected_reason",
    ),
    [
        (
            NOW - timedelta(seconds=1),
            NOW,
            False,
            RefreshQueueItemStatus.FULFILLED_NO_CHANGE,
            RefreshReturnReason.RELIABLE_NO_CHANGE,
        ),
        (
            None,
            NOW,
            False,
            RefreshQueueItemStatus.UNRESOLVED,
            RefreshReturnReason.NO_CHANGE_BASELINE_MISSING,
        ),
        (
            NOW,
            NOW,
            False,
            RefreshQueueItemStatus.STALE_RETURN,
            RefreshReturnReason.ACQUISITION_NOT_NEWER_THAN_BASELINE,
        ),
        (
            NOW - timedelta(seconds=1),
            NOW,
            True,
            RefreshQueueItemStatus.UNRESOLVED,
            RefreshReturnReason.ACQUISITION_CONFIRMATION_REQUIRED,
        ),
        (
            NOW - timedelta(seconds=1),
            None,
            False,
            RefreshQueueItemStatus.UNRESOLVED,
            RefreshReturnReason.ACQUISITION_MISSING,
        ),
    ],
)
def test_no_change_requires_confirmed_acquisition_strictly_after_nonnull_baseline(
    baseline: datetime | None,
    acquisition: datetime | None,
    confirmation_required: bool,
    expected_status: RefreshQueueItemStatus,
    expected_reason: RefreshReturnReason,
) -> None:
    account_id = uuid4()

    result = reconcile_refresh_return(
        [
            _row(
                account_id,
                acquisition=acquisition,
                confirmation_required=confirmation_required,
            )
        ],
        [_item(account_id, baseline=baseline)],
    )

    assert result.item_evidence[0].expected_status is expected_status
    assert result.item_evidence[0].reason is expected_reason


@pytest.mark.parametrize(
    ("row", "expected_reason"),
    [
        (
            lambda account_id: _row(
                account_id,
                action=ImportRowAction.UPDATE,
                change_summary=ChangeSummary(),
            ),
            RefreshReturnReason.ACTION_CHANGE_SUMMARY_MISMATCH,
        ),
        (
            lambda account_id: _row(
                account_id,
                action=ImportRowAction.NO_CHANGE,
                change_summary=_changed_summary(),
            ),
            RefreshReturnReason.ACTION_CHANGE_SUMMARY_MISMATCH,
        ),
        (
            lambda account_id: _row(account_id, action=ImportRowAction.ERROR),
            RefreshReturnReason.ACTION_NOT_FULFILLABLE,
        ),
        (
            lambda account_id: _row(account_id, action=ImportRowAction.MANUAL_REVIEW),
            RefreshReturnReason.ACTION_NOT_FULFILLABLE,
        ),
    ],
)
def test_unique_post_match_plan_problems_become_unresolved(
    row: object,
    expected_reason: RefreshReturnReason,
) -> None:
    account_id = uuid4()
    planned_row = row(account_id)  # type: ignore[operator]

    result = reconcile_refresh_return([planned_row], [_item(account_id)])

    assert result.item_evidence[0].expected_status is RefreshQueueItemStatus.UNRESOLVED
    assert result.item_evidence[0].reason is expected_reason


def test_skip_non_owner_non_unique_outside_queue_and_missing_return_stay_separate() -> None:
    queue_account_id = uuid4()
    outside_account_id = uuid4()
    item = _item(queue_account_id, status=RefreshQueueItemStatus.STALE_RETURN)
    skip = _row(
        queue_account_id,
        action=ImportRowAction.SKIP,
        owner_effective=False,
        locator=_locator(position=1),
    )
    non_owner = _row(
        queue_account_id,
        action=ImportRowAction.UPDATE,
        owner_effective=False,
        change_summary=_changed_summary(),
        locator=_locator(position=2),
    )
    non_unique = _row(
        None,
        action=ImportRowAction.MANUAL_REVIEW,
        locator=_locator(position=3),
    )
    outside = _row(
        outside_account_id,
        action=ImportRowAction.UPDATE,
        change_summary=_changed_summary(),
        locator=_locator(position=4),
    )

    result = reconcile_refresh_return([outside, non_unique, non_owner, skip], [item])

    assert [evidence.outcome for evidence in result.row_evidence] == [
        RefreshReturnRowOutcome.PENDING,
        RefreshReturnRowOutcome.PENDING,
        RefreshReturnRowOutcome.PENDING,
        RefreshReturnRowOutcome.OUTSIDE_QUEUE,
    ]
    assert [evidence.reason for evidence in result.row_evidence] == [
        RefreshReturnReason.ROW_ACTION_STAYS_PENDING,
        RefreshReturnReason.ROW_NOT_OWNER_EFFECTIVE,
        RefreshReturnReason.ROW_NOT_UNIQUELY_MATCHED,
        RefreshReturnReason.QUEUE_ITEM_NOT_FOUND,
    ]
    missing = result.item_evidence[0]
    assert missing.reason is RefreshReturnReason.MISSING_RETURN
    assert missing.expected_status is RefreshQueueItemStatus.STALE_RETURN
    assert missing.claimant_locator is None
    assert result.summary.model_dump(mode="json") == {
        "schema_version": 1,
        "row_count": 4,
        "queue_item_count": 1,
        "matched_row_count": 0,
        "outside_queue_row_count": 1,
        "pending_row_count": 3,
        "queue_items_with_return_count": 0,
        "queue_items_without_return_count": 1,
        "expected_fulfilled_changed_count": 0,
        "expected_fulfilled_no_change_count": 0,
        "expected_stale_return_count": 0,
        "expected_unresolved_count": 0,
        "unchanged_terminal_item_count": 0,
        "conflict_item_count": 0,
    }


def test_multiple_owner_rows_are_unresolved_with_stable_earliest_single_claimant() -> None:
    account_id = uuid4()
    item = _item(account_id)
    later_changed = _row(
        account_id,
        action=ImportRowAction.UPDATE,
        change_summary=_changed_summary(),
        locator=_locator(position=2, row_number=9),
    )
    earlier_no_change = _row(
        account_id,
        action=ImportRowAction.NO_CHANGE,
        locator=_locator(position=1, row_number=20),
    )

    result = reconcile_refresh_return([later_changed, earlier_no_change], [item])

    decision = result.item_evidence[0]
    assert decision.expected_status is RefreshQueueItemStatus.UNRESOLVED
    assert decision.reason is RefreshReturnReason.MULTIPLE_OWNER_ROWS
    assert decision.matching_row_count == 2
    assert decision.claimant_locator == earlier_no_change.locator
    assert result.claimed_items == (decision,)
    assert [evidence.is_last_return_claimant for evidence in result.row_evidence] == [True, False]
    assert {evidence.reason for evidence in result.row_evidence} == {
        RefreshReturnReason.MULTIPLE_OWNER_ROWS
    }
    assert result.summary.conflict_item_count == 1
    assert result.summary.expected_unresolved_count == 1


@pytest.mark.parametrize(
    "terminal_status",
    [
        RefreshQueueItemStatus.FULFILLED_CHANGED,
        RefreshQueueItemStatus.FULFILLED_NO_CHANGE,
        RefreshQueueItemStatus.CANCELLED,
    ],
)
def test_terminal_items_are_immutable_and_do_not_accept_last_return(
    terminal_status: RefreshQueueItemStatus,
) -> None:
    account_id = uuid4()
    row = _row(
        account_id,
        action=ImportRowAction.UPDATE,
        change_summary=_changed_summary(),
    )

    result = reconcile_refresh_return([row], [_item(account_id, status=terminal_status)])

    assert result.item_evidence[0].current_status is terminal_status
    assert result.item_evidence[0].expected_status is terminal_status
    assert result.item_evidence[0].claimant_locator is None
    assert result.row_evidence[0].outcome is RefreshReturnRowOutcome.TERMINAL_ITEM
    assert result.claimed_items == ()
    assert result.summary.unchanged_terminal_item_count == 1


def test_source_is_part_of_the_exclusive_match_key() -> None:
    account_id = uuid4()
    item = _item(account_id)

    result = reconcile_refresh_return(
        [
            _row(
                account_id,
                source=DataSource.GENERIC,
                action=ImportRowAction.UPDATE,
                change_summary=_changed_summary(),
            )
        ],
        [item],
    )

    assert result.row_evidence[0].outcome is RefreshReturnRowOutcome.OUTSIDE_QUEUE
    assert result.item_evidence[0].reason is RefreshReturnReason.MISSING_RETURN


def test_contracts_reject_naive_time_and_duplicate_item_match_keys() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _row(uuid4(), acquisition=datetime(2026, 8, 13, 12, 0))

    account_id = uuid4()
    queue_id = uuid4()
    with pytest.raises(ValueError, match="account/source keys"):
        reconcile_refresh_return(
            [],
            [
                _item(account_id, queue_id=queue_id),
                _item(account_id, queue_id=queue_id),
            ],
        )


def test_2000_item_mixed_reconciliation_is_deterministic_partitioned_and_bounded() -> None:
    queue_id = UUID(int=1)
    baseline = NOW - timedelta(days=1)
    changed_summary = _changed_summary()
    items: list[RefreshReturnQueueItem] = []
    rows: list[RefreshReturnRow] = []

    def stable_locator(index: int, *, competitor: bool = False) -> PreviewRowLocator:
        offset = index + (10_000 if competitor else 0)
        return PreviewRowLocator(
            import_job_file_id=UUID(int=10_000 + offset),
            file_position=2 if competitor else 1,
            row_number=2 + index,
            import_row_id=UUID(int=100_000 + offset),
        )

    for index in range(2_000):
        account_id = UUID(int=1_000_000 + index)
        item_status = (
            RefreshQueueItemStatus.FULFILLED_CHANGED
            if index % 10 == 7
            else RefreshQueueItemStatus.PENDING
        )
        item_baseline = None if index % 10 == 4 else baseline
        items.append(
            _item(
                account_id,
                baseline=item_baseline,
                status=item_status,
                queue_id=queue_id,
                item_id=UUID(int=2_000_000 + index),
            )
        )
        category = index % 10
        if category == 0:
            rows.append(
                _row(
                    account_id,
                    action=ImportRowAction.UPDATE,
                    change_summary=changed_summary,
                    locator=stable_locator(index),
                )
            )
        elif category == 1:
            rows.append(_row(account_id, locator=stable_locator(index)))
        elif category == 2:
            rows.append(
                _row(
                    account_id,
                    action=ImportRowAction.UPDATE,
                    acquisition=baseline,
                    change_summary=changed_summary,
                    locator=stable_locator(index),
                )
            )
        elif category == 3:
            rows.append(
                _row(
                    account_id,
                    confirmation_required=True,
                    locator=stable_locator(index),
                )
            )
        elif category == 4:
            rows.append(_row(account_id, locator=stable_locator(index)))
        elif category == 5:
            continue
        elif category == 6:
            rows.append(
                _row(
                    account_id,
                    action=ImportRowAction.SKIP,
                    owner_effective=False,
                    locator=stable_locator(index),
                )
            )
        elif category == 7:
            rows.append(_row(account_id, locator=stable_locator(index)))
        elif category == 8:
            rows.extend(
                (
                    _row(account_id, locator=stable_locator(index)),
                    _row(
                        account_id,
                        action=ImportRowAction.UPDATE,
                        change_summary=changed_summary,
                        locator=stable_locator(index, competitor=True),
                    ),
                )
            )
        else:
            rows.append(
                _row(
                    UUID(int=3_000_000 + index),
                    action=ImportRowAction.UPDATE,
                    change_summary=changed_summary,
                    locator=stable_locator(index),
                )
            )

    started = perf_counter()
    first = reconcile_refresh_return(reversed(rows), reversed(items))
    first_elapsed = perf_counter() - started
    started = perf_counter()
    repeated = reconcile_refresh_return(rows, items)
    repeated_elapsed = perf_counter() - started

    assert first.model_dump(mode="json") == repeated.model_dump(mode="json")
    assert first.summary.model_dump(mode="json") == {
        "schema_version": 1,
        "row_count": 2_000,
        "queue_item_count": 2_000,
        "matched_row_count": 1_600,
        "outside_queue_row_count": 200,
        "pending_row_count": 200,
        "queue_items_with_return_count": 1_400,
        "queue_items_without_return_count": 600,
        "expected_fulfilled_changed_count": 200,
        "expected_fulfilled_no_change_count": 200,
        "expected_stale_return_count": 200,
        "expected_unresolved_count": 600,
        "unchanged_terminal_item_count": 200,
        "conflict_item_count": 200,
    }
    claimed_items = first.claimed_items
    claimant_rows = [row for row in first.row_evidence if row.is_last_return_claimant]
    assert len(claimed_items) == 1_200
    assert len(claimant_rows) == 1_200
    assert len({item.queue_item_id for item in claimed_items}) == 1_200
    assert len({item.claimant_locator.import_row_id for item in claimed_items}) == 1_200
    assert len({row.locator.import_row_id for row in claimant_rows}) == 1_200
    assert max(first_elapsed, repeated_elapsed) < 2.0
    print(
        "TASK9_REFRESH_RECONCILIATION_2000 "
        + json.dumps(
            {
                "first_seconds": round(first_elapsed, 6),
                "repeated_seconds": round(repeated_elapsed, 6),
                "items": 2_000,
                "rows": 2_000,
                "claimed_items": len(claimed_items),
            },
            sort_keys=True,
        )
    )


def test_repository_projection_omits_identity_snapshot_and_normalizes_time() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        statements: list[str] = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def capture_statement(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            statements.append(statement)

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            queue_id = uuid4()
            item_id = uuid4()
            account_id = uuid4()
            baseline = NOW - timedelta(days=2)
            async with factory() as session:
                session.add(
                    RefreshQueue(
                        id=queue_id,
                        department_id=uuid4(),
                        created_by_operator_id=uuid4(),
                        status=RefreshQueueStatus.OPEN,
                        as_of=NOW,
                        requested_limit=1,
                        today_total_limit=1,
                        refresh_limit=1,
                        policy_version=1,
                        criteria_snapshot={"schema_version": 1},
                    )
                )
                session.add(
                    RefreshQueueItem(
                        id=item_id,
                        department_id=uuid4(),
                        queue_id=queue_id,
                        influencer_id=uuid4(),
                        platform_account_id=account_id,
                        source=DataSource.HUITUN,
                        priority_tier=1,
                        priority_reasons=["FRESHNESS_UNKNOWN"],
                        identity_snapshot={"must_never_be_read": "queue identity"},
                        baseline_last_observed_at=baseline,
                        baseline_source_updated_at=None,
                        status=RefreshQueueItemStatus.PENDING,
                    )
                )
                await session.commit()
                statements.clear()

                records = await RefreshQueueRepository(session).list_reconciliation_items(
                    queue_id,
                    for_update=True,
                )

                assert records == [
                    RefreshReturnQueueItem(
                        queue_id=queue_id,
                        queue_item_id=item_id,
                        platform_account_id=account_id,
                        source=DataSource.HUITUN,
                        baseline_last_observed_at=baseline,
                        status=RefreshQueueItemStatus.PENDING,
                    )
                ]
                projection_selects = [
                    statement
                    for statement in statements
                    if statement.lstrip().upper().startswith("SELECT")
                    and "refresh_queue_items" in statement
                ]
                assert len(projection_selects) == 1
                assert "identity_snapshot" not in projection_selects[0]
        finally:
            await engine.dispose()

    asyncio.run(scenario())
