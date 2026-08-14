"""Pure Refresh Queue return matching and expected-fulfillment evidence.

The module starts *after* Phase 1B Hard Match.  It deliberately accepts only
the matched platform-account UUID and the canonical record source; Queue
identity snapshots are not part of any input contract and therefore cannot be
used as a fallback matcher.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from backend_core.imports.contracts import FrozenContract
from backend_core.imports.enums import ImportRowAction
from backend_core.imports.preview_domain import (
    ChangeSummary,
    PreviewRowLocator,
    has_effective_changes,
)
from backend_core.influencers.enums import DataSource
from backend_core.refresh.enums import RefreshQueueItemStatus

NonnegativeStrictInt = Annotated[StrictInt, Field(ge=0)]

_RECONCILABLE_STATUSES = frozenset(
    {
        RefreshQueueItemStatus.PENDING,
        RefreshQueueItemStatus.STALE_RETURN,
        RefreshQueueItemStatus.UNRESOLVED,
    }
)
_FULFILLED_STATUSES = frozenset(
    {
        RefreshQueueItemStatus.FULFILLED_CHANGED,
        RefreshQueueItemStatus.FULFILLED_NO_CHANGE,
    }
)
_TERMINAL_STATUSES = _FULFILLED_STATUSES | {RefreshQueueItemStatus.CANCELLED}


def _require_aware(value: datetime | None, *, field_name: str) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field_name} must include a timezone")
    return value


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


class RefreshReturnReason(StrEnum):
    """Closed, non-sensitive explanation codes for Preview and Confirm."""

    ROW_NOT_OWNER_EFFECTIVE = "ROW_NOT_OWNER_EFFECTIVE"
    ROW_NOT_UNIQUELY_MATCHED = "ROW_NOT_UNIQUELY_MATCHED"
    ROW_ACTION_STAYS_PENDING = "ROW_ACTION_STAYS_PENDING"
    QUEUE_ITEM_NOT_FOUND = "QUEUE_ITEM_NOT_FOUND"
    QUEUE_ITEM_TERMINAL = "QUEUE_ITEM_TERMINAL"
    MISSING_RETURN = "MISSING_RETURN"
    MULTIPLE_OWNER_ROWS = "MULTIPLE_OWNER_ROWS"
    ACTION_CHANGE_SUMMARY_MISMATCH = "ACTION_CHANGE_SUMMARY_MISMATCH"
    ACTION_NOT_FULFILLABLE = "ACTION_NOT_FULFILLABLE"
    ACQUISITION_CONFIRMATION_REQUIRED = "ACQUISITION_CONFIRMATION_REQUIRED"
    ACQUISITION_MISSING = "ACQUISITION_MISSING"
    ACQUISITION_NOT_NEWER_THAN_BASELINE = "ACQUISITION_NOT_NEWER_THAN_BASELINE"
    NO_CHANGE_BASELINE_MISSING = "NO_CHANGE_BASELINE_MISSING"
    EFFECTIVE_CHANGES = "EFFECTIVE_CHANGES"
    RELIABLE_NO_CHANGE = "RELIABLE_NO_CHANGE"


class RefreshReturnRowOutcome(StrEnum):
    """Whether a row participates in the linked Queue reconciliation."""

    PENDING = "pending"
    OUTSIDE_QUEUE = "outside_queue"
    EXPECTED_FULFILLMENT = "expected_fulfillment"
    TERMINAL_ITEM = "terminal_item"


class RefreshReturnRow(FrozenContract):
    """One already-planned row, with no Queue-derived identity material."""

    locator: PreviewRowLocator
    source: DataSource
    action: ImportRowAction
    matched_platform_account_id: UUID | None
    is_owner_effective: StrictBool
    source_acquired_at: datetime | None
    source_acquired_at_confirmation_required: StrictBool
    change_summary: ChangeSummary

    @field_validator("source_acquired_at")
    @classmethod
    def validate_acquisition(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value, field_name="source_acquired_at")


class RefreshReturnQueueItem(FrozenContract):
    """Minimal Queue Item projection required for reconciliation.

    In particular, ``identity_snapshot`` is intentionally absent.
    """

    queue_id: UUID
    queue_item_id: UUID
    platform_account_id: UUID
    source: DataSource
    baseline_last_observed_at: datetime | None
    status: RefreshQueueItemStatus

    @field_validator("baseline_last_observed_at")
    @classmethod
    def validate_baseline(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value, field_name="baseline_last_observed_at")


class RefreshReturnRowEvidence(FrozenContract):
    """Additive, non-sensitive Preview evidence for one physical row."""

    locator: PreviewRowLocator
    outcome: RefreshReturnRowOutcome
    queue_item_id: UUID | None = None
    expected_status: RefreshQueueItemStatus | None = None
    reason: RefreshReturnReason
    is_last_return_claimant: StrictBool = False

    @model_validator(mode="after")
    def validate_shape(self) -> RefreshReturnRowEvidence:
        if self.outcome in {
            RefreshReturnRowOutcome.PENDING,
            RefreshReturnRowOutcome.OUTSIDE_QUEUE,
        }:
            if self.queue_item_id is not None or self.expected_status is not None:
                raise ValueError("unassociated row evidence cannot reference a Queue Item")
            if self.is_last_return_claimant:
                raise ValueError("unassociated row evidence cannot claim Last Return")
        elif self.outcome is RefreshReturnRowOutcome.TERMINAL_ITEM:
            if self.queue_item_id is None or self.expected_status not in _TERMINAL_STATUSES:
                raise ValueError("terminal row evidence must preserve a terminal Queue Item")
            if self.is_last_return_claimant:
                raise ValueError("terminal Queue Items cannot accept a new Last Return")
        else:
            if self.queue_item_id is None or self.expected_status not in {
                RefreshQueueItemStatus.FULFILLED_CHANGED,
                RefreshQueueItemStatus.FULFILLED_NO_CHANGE,
                RefreshQueueItemStatus.STALE_RETURN,
                RefreshQueueItemStatus.UNRESOLVED,
            }:
                raise ValueError("matched row evidence requires a reconcilable expected status")
        return self


class RefreshReturnItemEvidence(FrozenContract):
    """One and only one expected decision for each Queue Item."""

    queue_item_id: UUID
    current_status: RefreshQueueItemStatus
    expected_status: RefreshQueueItemStatus
    matching_row_count: NonnegativeStrictInt
    claimant_locator: PreviewRowLocator | None = None
    reason: RefreshReturnReason

    @model_validator(mode="after")
    def validate_shape(self) -> RefreshReturnItemEvidence:
        if self.reason is RefreshReturnReason.MISSING_RETURN:
            if self.matching_row_count != 0 or self.claimant_locator is not None:
                raise ValueError("missing-return evidence cannot claim a row")
            if self.expected_status is not self.current_status:
                raise ValueError("missing return must preserve the current Item status")
            return self
        if self.reason is RefreshReturnReason.QUEUE_ITEM_TERMINAL:
            if self.current_status not in _TERMINAL_STATUSES:
                raise ValueError("terminal evidence requires a terminal current status")
            if self.matching_row_count < 1 or self.claimant_locator is not None:
                raise ValueError("terminal evidence records matches without claiming a row")
            if self.expected_status is not self.current_status:
                raise ValueError("terminal Item status cannot change")
            return self
        if self.current_status not in _RECONCILABLE_STATUSES:
            raise ValueError("only active Queue Items can receive a return decision")
        if self.matching_row_count < 1 or self.claimant_locator is None:
            raise ValueError("active return decisions require exactly one claimant locator")
        if self.expected_status not in {
            RefreshQueueItemStatus.FULFILLED_CHANGED,
            RefreshQueueItemStatus.FULFILLED_NO_CHANGE,
            RefreshQueueItemStatus.STALE_RETURN,
            RefreshQueueItemStatus.UNRESOLVED,
        }:
            raise ValueError("active return decision has an invalid expected status")
        return self

    @property
    def claims_last_return(self) -> bool:
        return self.claimant_locator is not None


class RefreshReturnPreviewSummary(FrozenContract):
    """Small additive summary suitable for nesting under an Import Preview."""

    schema_version: Literal[1] = 1
    row_count: NonnegativeStrictInt
    queue_item_count: NonnegativeStrictInt
    matched_row_count: NonnegativeStrictInt
    outside_queue_row_count: NonnegativeStrictInt
    pending_row_count: NonnegativeStrictInt
    queue_items_with_return_count: NonnegativeStrictInt
    queue_items_without_return_count: NonnegativeStrictInt
    expected_fulfilled_changed_count: NonnegativeStrictInt
    expected_fulfilled_no_change_count: NonnegativeStrictInt
    expected_stale_return_count: NonnegativeStrictInt
    expected_unresolved_count: NonnegativeStrictInt
    unchanged_terminal_item_count: NonnegativeStrictInt
    conflict_item_count: NonnegativeStrictInt

    @model_validator(mode="after")
    def validate_partitions(self) -> RefreshReturnPreviewSummary:
        if self.row_count != (
            self.matched_row_count + self.outside_queue_row_count + self.pending_row_count
        ):
            raise ValueError("Refresh return row counts must form a partition")
        if self.queue_item_count != (
            self.queue_items_with_return_count + self.queue_items_without_return_count
        ):
            raise ValueError("Refresh return Item presence counts must form a partition")
        if self.queue_item_count != (
            self.expected_fulfilled_changed_count
            + self.expected_fulfilled_no_change_count
            + self.expected_stale_return_count
            + self.expected_unresolved_count
            + self.unchanged_terminal_item_count
            + self.queue_items_without_return_count
        ):
            raise ValueError("Refresh return expected Item counts must form a partition")
        if self.conflict_item_count > self.expected_unresolved_count:
            raise ValueError("conflict Items must be a subset of expected unresolved Items")
        return self


class RefreshReturnPreview(FrozenContract):
    """Complete pure reconciliation output; it does not mutate Queue state."""

    summary: RefreshReturnPreviewSummary
    row_evidence: tuple[RefreshReturnRowEvidence, ...]
    item_evidence: tuple[RefreshReturnItemEvidence, ...]

    @model_validator(mode="after")
    def validate_evidence_links(self) -> RefreshReturnPreview:
        row_ids = [item.locator.import_row_id for item in self.row_evidence]
        if len(row_ids) != len(set(row_ids)):
            raise ValueError("row evidence must contain each Import Row once")
        item_ids = [item.queue_item_id for item in self.item_evidence]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("Item evidence must contain each Queue Item once")
        claimants = {
            item.queue_item_id: item.claimant_locator
            for item in self.item_evidence
            if item.claimant_locator is not None
        }
        for item_id, locator in claimants.items():
            matching_claimants = [
                row
                for row in self.row_evidence
                if row.queue_item_id == item_id and row.is_last_return_claimant
            ]
            if len(matching_claimants) != 1 or matching_claimants[0].locator != locator:
                raise ValueError("each Item decision must have one matching row claimant")
        if any(
            row.is_last_return_claimant and row.queue_item_id not in claimants
            for row in self.row_evidence
        ):
            raise ValueError("row claimant must reference an Item decision")
        return self

    @property
    def claimed_items(self) -> tuple[RefreshReturnItemEvidence, ...]:
        """Return application-ready Item decisions in stable order."""

        return tuple(item for item in self.item_evidence if item.claims_last_return)


def _classify_single_return(
    row: RefreshReturnRow,
    item: RefreshReturnQueueItem,
) -> tuple[RefreshQueueItemStatus, RefreshReturnReason]:
    if row.action not in {ImportRowAction.UPDATE, ImportRowAction.NO_CHANGE}:
        return RefreshQueueItemStatus.UNRESOLVED, RefreshReturnReason.ACTION_NOT_FULFILLABLE

    changed = has_effective_changes(row.change_summary)
    if (row.action is ImportRowAction.UPDATE) != changed:
        return (
            RefreshQueueItemStatus.UNRESOLVED,
            RefreshReturnReason.ACTION_CHANGE_SUMMARY_MISMATCH,
        )
    if row.source_acquired_at_confirmation_required:
        return (
            RefreshQueueItemStatus.UNRESOLVED,
            RefreshReturnReason.ACQUISITION_CONFIRMATION_REQUIRED,
        )
    if row.source_acquired_at is None:
        return RefreshQueueItemStatus.UNRESOLVED, RefreshReturnReason.ACQUISITION_MISSING

    acquisition = _utc(row.source_acquired_at)
    baseline = (
        _utc(item.baseline_last_observed_at) if item.baseline_last_observed_at is not None else None
    )
    if baseline is not None and acquisition <= baseline:
        return (
            RefreshQueueItemStatus.STALE_RETURN,
            RefreshReturnReason.ACQUISITION_NOT_NEWER_THAN_BASELINE,
        )
    if changed:
        return RefreshQueueItemStatus.FULFILLED_CHANGED, RefreshReturnReason.EFFECTIVE_CHANGES
    if baseline is None:
        return (
            RefreshQueueItemStatus.UNRESOLVED,
            RefreshReturnReason.NO_CHANGE_BASELINE_MISSING,
        )
    return (
        RefreshQueueItemStatus.FULFILLED_NO_CHANGE,
        RefreshReturnReason.RELIABLE_NO_CHANGE,
    )


def reconcile_refresh_return(
    rows: Iterable[RefreshReturnRow],
    queue_items: Iterable[RefreshReturnQueueItem],
) -> RefreshReturnPreview:
    """Match planned rows to one linked Queue and classify expected outcomes.

    The function performs no persistence.  Only ``(matched_platform_account_id,
    source)`` is used for matching.  If multiple otherwise eligible rows map to
    one active Item, the Item becomes unresolved and the stable earliest
    locator is the sole Last Return claimant.
    """

    ordered_rows = tuple(sorted(rows, key=lambda item: item.locator.sort_key))
    ordered_items = tuple(
        sorted(
            queue_items,
            key=lambda item: (
                str(item.platform_account_id),
                item.source.value,
                str(item.queue_item_id),
            ),
        )
    )

    physical_row_ids = [item.locator.physical_identity for item in ordered_rows]
    import_row_ids = [item.locator.import_row_id for item in ordered_rows]
    if len(physical_row_ids) != len(set(physical_row_ids)) or len(import_row_ids) != len(
        set(import_row_ids)
    ):
        raise ValueError("Refresh return rows must have unique physical and Import Row locators")

    queue_ids = {item.queue_id for item in ordered_items}
    if len(queue_ids) > 1:
        raise ValueError("Refresh return Queue Items must belong to one Queue")
    item_ids = [item.queue_item_id for item in ordered_items]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("Refresh return Queue Item IDs must be unique")

    items_by_key: dict[tuple[UUID, DataSource], RefreshReturnQueueItem] = {}
    for item in ordered_items:
        key = (item.platform_account_id, item.source)
        if key in items_by_key:
            raise ValueError("Refresh return Queue Item account/source keys must be unique")
        items_by_key[key] = item

    matched_rows: dict[UUID, list[RefreshReturnRow]] = defaultdict(list)
    evidence_by_row: dict[UUID, RefreshReturnRowEvidence] = {}
    for row in ordered_rows:
        if row.action is ImportRowAction.SKIP:
            evidence_by_row[row.locator.import_row_id] = RefreshReturnRowEvidence(
                locator=row.locator,
                outcome=RefreshReturnRowOutcome.PENDING,
                reason=RefreshReturnReason.ROW_ACTION_STAYS_PENDING,
            )
            continue
        if not row.is_owner_effective:
            evidence_by_row[row.locator.import_row_id] = RefreshReturnRowEvidence(
                locator=row.locator,
                outcome=RefreshReturnRowOutcome.PENDING,
                reason=RefreshReturnReason.ROW_NOT_OWNER_EFFECTIVE,
            )
            continue
        if row.matched_platform_account_id is None:
            evidence_by_row[row.locator.import_row_id] = RefreshReturnRowEvidence(
                locator=row.locator,
                outcome=RefreshReturnRowOutcome.PENDING,
                reason=RefreshReturnReason.ROW_NOT_UNIQUELY_MATCHED,
            )
            continue
        matched_item = items_by_key.get((row.matched_platform_account_id, row.source))
        if matched_item is None:
            evidence_by_row[row.locator.import_row_id] = RefreshReturnRowEvidence(
                locator=row.locator,
                outcome=RefreshReturnRowOutcome.OUTSIDE_QUEUE,
                reason=RefreshReturnReason.QUEUE_ITEM_NOT_FOUND,
            )
            continue
        matched_rows[matched_item.queue_item_id].append(row)

    item_evidence: list[RefreshReturnItemEvidence] = []
    for item in ordered_items:
        matches = matched_rows.get(item.queue_item_id, [])
        if not matches:
            item_evidence.append(
                RefreshReturnItemEvidence(
                    queue_item_id=item.queue_item_id,
                    current_status=item.status,
                    expected_status=item.status,
                    matching_row_count=0,
                    reason=RefreshReturnReason.MISSING_RETURN,
                )
            )
            continue
        if item.status in _TERMINAL_STATUSES:
            item_evidence.append(
                RefreshReturnItemEvidence(
                    queue_item_id=item.queue_item_id,
                    current_status=item.status,
                    expected_status=item.status,
                    matching_row_count=len(matches),
                    reason=RefreshReturnReason.QUEUE_ITEM_TERMINAL,
                )
            )
            for row in matches:
                evidence_by_row[row.locator.import_row_id] = RefreshReturnRowEvidence(
                    locator=row.locator,
                    outcome=RefreshReturnRowOutcome.TERMINAL_ITEM,
                    queue_item_id=item.queue_item_id,
                    expected_status=item.status,
                    reason=RefreshReturnReason.QUEUE_ITEM_TERMINAL,
                )
            continue

        claimant = matches[0]
        if len(matches) > 1:
            expected_status = RefreshQueueItemStatus.UNRESOLVED
            reason = RefreshReturnReason.MULTIPLE_OWNER_ROWS
        else:
            expected_status, reason = _classify_single_return(claimant, item)
        item_evidence.append(
            RefreshReturnItemEvidence(
                queue_item_id=item.queue_item_id,
                current_status=item.status,
                expected_status=expected_status,
                matching_row_count=len(matches),
                claimant_locator=claimant.locator,
                reason=reason,
            )
        )
        for row in matches:
            evidence_by_row[row.locator.import_row_id] = RefreshReturnRowEvidence(
                locator=row.locator,
                outcome=RefreshReturnRowOutcome.EXPECTED_FULFILLMENT,
                queue_item_id=item.queue_item_id,
                expected_status=expected_status,
                reason=reason,
                is_last_return_claimant=row.locator == claimant.locator,
            )

    row_evidence = tuple(evidence_by_row[row.locator.import_row_id] for row in ordered_rows)
    item_evidence_tuple = tuple(item_evidence)
    matched_row_count = sum(
        item.outcome
        in {RefreshReturnRowOutcome.EXPECTED_FULFILLMENT, RefreshReturnRowOutcome.TERMINAL_ITEM}
        for item in row_evidence
    )
    outside_queue_row_count = sum(
        item.outcome is RefreshReturnRowOutcome.OUTSIDE_QUEUE for item in row_evidence
    )
    pending_row_count = sum(
        item.outcome is RefreshReturnRowOutcome.PENDING for item in row_evidence
    )
    queue_items_with_return_count = sum(item.matching_row_count > 0 for item in item_evidence_tuple)
    queue_items_without_return_count = sum(
        item.matching_row_count == 0 for item in item_evidence_tuple
    )

    def _claimed_status_count(status: RefreshQueueItemStatus) -> int:
        return sum(
            item.claims_last_return and item.expected_status is status
            for item in item_evidence_tuple
        )

    summary = RefreshReturnPreviewSummary(
        row_count=len(ordered_rows),
        queue_item_count=len(ordered_items),
        matched_row_count=matched_row_count,
        outside_queue_row_count=outside_queue_row_count,
        pending_row_count=pending_row_count,
        queue_items_with_return_count=queue_items_with_return_count,
        queue_items_without_return_count=queue_items_without_return_count,
        expected_fulfilled_changed_count=_claimed_status_count(
            RefreshQueueItemStatus.FULFILLED_CHANGED
        ),
        expected_fulfilled_no_change_count=_claimed_status_count(
            RefreshQueueItemStatus.FULFILLED_NO_CHANGE
        ),
        expected_stale_return_count=_claimed_status_count(RefreshQueueItemStatus.STALE_RETURN),
        expected_unresolved_count=_claimed_status_count(RefreshQueueItemStatus.UNRESOLVED),
        unchanged_terminal_item_count=sum(
            item.reason is RefreshReturnReason.QUEUE_ITEM_TERMINAL for item in item_evidence_tuple
        ),
        conflict_item_count=sum(
            item.reason is RefreshReturnReason.MULTIPLE_OWNER_ROWS for item in item_evidence_tuple
        ),
    )
    return RefreshReturnPreview(
        summary=summary,
        row_evidence=row_evidence,
        item_evidence=item_evidence_tuple,
    )


__all__ = [
    "RefreshReturnItemEvidence",
    "RefreshReturnPreview",
    "RefreshReturnPreviewSummary",
    "RefreshReturnQueueItem",
    "RefreshReturnReason",
    "RefreshReturnRow",
    "RefreshReturnRowEvidence",
    "RefreshReturnRowOutcome",
    "reconcile_refresh_return",
]
