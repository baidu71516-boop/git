"""One deterministic Phase 2 plan builder shared by Preview and Confirm.

The builder owns every input and derived fact that contributes to a Unified
Preview plan. Callers remain responsible for lifecycle guards, persistence,
business writes, and transaction completion.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.imports.batch_processor import (
    BatchImportProcessor,
    RevalidationMode,
    _StagingRow,
)
from backend_core.imports.bulk_repository import AccountSourceKey, PrefetchedImportState
from backend_core.imports.enums import ImportJobFileStatus, ImportRowAction
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.hashing import hash_document
from backend_core.imports.models import CollectionJob, ImportJob, ImportJobFile
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.planner import PlannedImportRow, identity_lock_keys
from backend_core.imports.preview_domain import (
    BatchPlanHashEntry,
    ChangeSummary,
    PreviewFileManifestEntry,
    PreviewRevisionContext,
    PreviewRowFact,
    PreviewRowHashInput,
    PreviewRowLocator,
    ScreeningEvaluation,
    ScreeningRulePayload,
    ScreeningRuleSnapshot,
    build_change_summary,
    evaluate_screening,
    has_effective_changes,
    hash_batch_plan,
    hash_preview_row,
    summarize_preview,
)
from backend_core.imports.repository import ImportRepository
from backend_core.imports.storage import StorageAdapter
from backend_core.refresh.enums import RefreshQueueStatus
from backend_core.refresh.reconciliation import (
    RefreshReturnPreview,
    RefreshReturnRow,
    reconcile_refresh_return,
)
from backend_core.refresh.repository import RefreshQueueRepository

_PLANNER_VERSION = "phase1b-v1"
_ADAPTER_VERSION = "phase2-v1"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class UnifiedPlanRow:
    """A canonical row, its shared Planner decision, and frozen identity."""

    staging: _StagingRow
    plan: PlannedImportRow
    locator: PreviewRowLocator
    row_plan_hash: str


@dataclass(frozen=True, slots=True)
class UnifiedPlanResult:
    """Complete deterministic plan evidence for one requested revision."""

    context: PreviewRevisionContext
    screening: ScreeningRuleSnapshot
    rows: tuple[UnifiedPlanRow, ...]
    prefetched_state: PrefetchedImportState
    summary: dict[str, Any]
    refresh_return: RefreshReturnPreview | None = None

    @property
    def staging_rows(self) -> tuple[_StagingRow, ...]:
        return tuple(row.staging for row in self.rows)

    @property
    def plans(self) -> tuple[PlannedImportRow, ...]:
        return tuple(row.plan for row in self.rows)


class UnifiedPlanBuilder:
    """Recompute the sole formal plan used by both Preview and Confirm."""

    def __init__(
        self,
        session: AsyncSession,
        storage: StorageAdapter,
        *,
        parser_limits: ParserLimits,
        max_batch_rows: int = 10_000,
    ) -> None:
        self.session = session
        self.storage = storage
        self.repository = ImportRepository(session)
        self.refresh_queue_repository = RefreshQueueRepository(session)
        self.batch = BatchImportProcessor(
            session,
            storage,
            parser_limits=parser_limits,
            max_batch_rows=max_batch_rows,
        )
        self.parser_limits = parser_limits
        self.max_batch_rows = max_batch_rows

    async def build(
        self,
        collection: CollectionJob,
        job: ImportJob,
        files: list[ImportJobFile],
        *,
        preview_revision: int,
        revalidation_mode: RevalidationMode,
    ) -> UnifiedPlanResult:
        """Revalidate frozen inputs and rebuild their deterministic formal plan."""

        included = [item for item in files if item.status is not ImportJobFileStatus.EXCLUDED]
        if not included:
            raise ImportDomainError(
                "IMPORT_PREVIEW_EMPTY",
                "At least one included file is required",
                status_code=409,
            )
        blocking = [item for item in included if item.status is not ImportJobFileStatus.READY]
        if blocking:
            raise ImportDomainError(
                "IMPORT_PREVIEW_BLOCKED",
                "All included files must be parsed successfully",
                status_code=409,
            )
        if job.refresh_queue_id is None and any(
            item.source_acquired_at_confirmation_required for item in included
        ):
            raise ImportDomainError(
                "SOURCE_ACQUIRED_AT_CONFIRMATION_REQUIRED",
                "Confirm source acquisition time before generating Preview",
                status_code=409,
            )

        screening = self.screening_snapshot(collection)
        manifest = await self.manifest(job, files)
        context = PreviewRevisionContext(
            import_job_id=job.id,
            preview_revision=preview_revision,
            collection_job_id=job.collection_job_id,
            source_type=job.source_type,
            refresh_queue_id=job.refresh_queue_id,
            files=manifest,
            screening=screening,
            planner_version=_PLANNER_VERSION,
            relevant_config={
                "adapter_version": _ADAPTER_VERSION,
                "max_batch_rows": self.max_batch_rows,
                "parser_limits": {
                    "max_xlsx_uncompressed_bytes": (self.parser_limits.max_xlsx_uncompressed_bytes),
                    "max_xlsx_entries": self.parser_limits.max_xlsx_entries,
                    "max_xlsx_compression_ratio": (self.parser_limits.max_xlsx_compression_ratio),
                    "max_rows": self.parser_limits.max_rows,
                    "max_columns": self.parser_limits.max_columns,
                    "max_cells": self.parser_limits.max_cells,
                    "max_cell_chars": self.parser_limits.max_cell_chars,
                    "max_warnings": self.parser_limits.max_warnings,
                },
            },
        )

        staging_rows = await self.batch.revalidate_ready_files(
            job,
            files,
            mode=revalidation_mode,
        )

        # The rows are locked by the caller. Refresh nevertheless makes the
        # comparison fail closed if the identity map was unexpectedly mutated
        # during storage I/O.
        await self.session.refresh(collection)
        await self.session.refresh(job)
        for item in files:
            await self.session.refresh(item)
        if self.screening_snapshot(collection) != screening:
            raise ImportDomainError(
                "IMPORT_PREVIEW_REVALIDATION_FAILED",
                "Screening rules changed during Preview revalidation",
            )
        if await self.manifest(job, files) != manifest:
            raise ImportDomainError(
                "IMPORT_PREVIEW_REVALIDATION_FAILED",
                "Preview file manifest changed during revalidation",
            )

        # All identity advisory locks precede the first Task 4 prefetch/Planner
        # read. ImportRepository sorts and deduplicates the lock keys.
        await self.repository.acquire_identity_locks(
            key
            for staged in staging_rows
            if not staged.initial_errors
            for key in identity_lock_keys(staged.record)
        )
        contact_lock_keys = {
            f"phase2:contact-value:{contact.type.value}:{contact.normalized_value}"
            for staged in staging_rows
            if not staged.initial_errors
            for contact in staged.record.contacts
            if contact.normalized_value
        }
        if contact_lock_keys:
            # Contact locks only serialize duplicate/protection facts. They are
            # never fed to Hard Matcher and therefore cannot turn email into an
            # identity match.
            await self.repository.acquire_identity_locks(contact_lock_keys)
        plans, prefetched_state = await self.batch.plan_preview_staging(
            job,
            staging_rows,
            preview_revision=preview_revision,
        )
        # Two incoming rows can use different hard-identity bases yet resolve
        # to the same existing account (for example platform ID versus profile
        # URL). Incoming locks then do not overlap. A stable account-level lock
        # set provides the shared serialization point; re-planning under those
        # locks observes any transaction that won before us.
        target_account_ids = {
            plan.matched_platform_account_id
            for plan in plans
            if plan.matched_platform_account_id is not None
        }
        if target_account_ids:
            await self.repository.acquire_identity_locks(
                (
                    *(f"phase2:target-account:{account_id}" for account_id in target_account_ids),
                    *(
                        f"phase2:target-influencer:{prefetched_state.accounts_by_id[account_id].influencer_id}"
                        for account_id in target_account_ids
                    ),
                )
            )
            plans, prefetched_state = await self.batch.plan_preview_staging(
                job,
                staging_rows,
                preview_revision=preview_revision,
            )
            replanned_target_ids = {
                plan.matched_platform_account_id
                for plan in plans
                if plan.matched_platform_account_id is not None
            }
            if not replanned_target_ids.issubset(target_account_ids):
                raise ImportDomainError(
                    "IMPORT_PREVIEW_REVALIDATION_FAILED",
                    "Matched account set changed while acquiring deterministic locks",
                )

        rows: list[UnifiedPlanRow] = []
        facts: list[PreviewRowFact] = []
        batch_entries: list[BatchPlanHashEntry] = []
        for staged, plan in zip(staging_rows, plans, strict=True):
            screening_evaluation = None
            is_duplicate = bool((plan.merge_plan or {}).get("batch_duplicate"))
            if plan.action not in {ImportRowAction.ERROR, ImportRowAction.SKIP}:
                screening_evaluation = evaluate_screening(staged.record, screening)
                plan.merge_plan["screening"] = screening_evaluation.model_dump(mode="json")

            existing_source_data = None
            existing_metrics = None
            previous_observation = None
            if plan.matched_platform_account_id is not None:
                account_source = AccountSourceKey(
                    plan.matched_platform_account_id,
                    staged.record.source,
                )
                source_state = prefetched_state.source_states.get(account_source)
                current_metrics = prefetched_state.current_metrics.get(account_source)
                existing_source_data = source_state.source_data if source_state else None
                existing_metrics = current_metrics.metrics if current_metrics else None
                previous_observation = prefetched_state.last_confirmed_observations.get(
                    account_source
                )
            normalized_previous_observation = _as_utc(previous_observation)
            plan.preconditions["last_confirmed_observation_at"] = (
                normalized_previous_observation.isoformat()
                if normalized_previous_observation is not None
                else None
            )
            if plan.action in {ImportRowAction.ERROR, ImportRowAction.SKIP}:
                change_summary = ChangeSummary()
            else:
                change_summary = build_change_summary(
                    staged.record,
                    plan.merge_plan,
                    plan.preconditions,
                    source_acquired_at=(
                        _as_utc(staged.occurrence.source_acquired_at)
                        if plan.action
                        in {
                            ImportRowAction.CREATE,
                            ImportRowAction.UPDATE,
                            ImportRowAction.NO_CHANGE,
                        }
                        else None
                    ),
                    previous_source_acquired_at=normalized_previous_observation,
                    existing_source_data=existing_source_data,
                    existing_metrics=existing_metrics,
                )
            if plan.action is ImportRowAction.UPDATE and not has_effective_changes(change_summary):
                plan = replace(plan, action=ImportRowAction.NO_CHANGE)
            plan.merge_plan["change_summary"] = change_summary.model_dump(mode="json")
            plan.merge_plan["preview_context_hash"] = context.context_hash
            locator = PreviewRowLocator(
                import_job_file_id=staged.occurrence.id,
                file_position=staged.occurrence.position,
                row_number=staged.row_number,
                import_row_id=staged.import_row_id,
            )
            duplicate_owner = self.duplicate_owner_locator(plan.merge_plan)
            plan_hash = self.hash_row(
                context=context,
                staged=staged,
                plan=plan,
                locator=locator,
                duplicate_owner=duplicate_owner,
                screening=screening_evaluation,
                change_summary=change_summary,
            )
            possible_duplicate = any(
                warning.get("code") == "POSSIBLE_DUPLICATE_CONTACT" for warning in plan.warnings
            ) or self.plan_has_possible_duplicate_contact(plan.merge_plan)
            facts.append(
                PreviewRowFact(
                    action=plan.action,
                    has_warning=bool(plan.warnings),
                    is_batch_duplicate=is_duplicate,
                    possible_duplicate_contact=possible_duplicate,
                    screening_result=(
                        screening_evaluation.result if screening_evaluation is not None else None
                    ),
                )
            )
            batch_entries.append(BatchPlanHashEntry(locator=locator, row_plan_hash=plan_hash))
            rows.append(
                UnifiedPlanRow(
                    staging=staged,
                    plan=plan,
                    locator=locator,
                    row_plan_hash=plan_hash,
                )
            )

        refresh_return = await self.refresh_return_preview(
            job,
            tuple(rows),
            for_update=revalidation_mode is RevalidationMode.CONFIRM_PRESERVE,
        )
        if refresh_return is not None:
            evidence_by_row_id = {
                evidence.locator.import_row_id: evidence for evidence in refresh_return.row_evidence
            }
            batch_entries = []
            reconciled_rows: list[UnifiedPlanRow] = []
            for planned_row in rows:
                evidence = evidence_by_row_id[planned_row.locator.import_row_id]
                planned_row.plan.merge_plan["refresh_return"] = evidence.model_dump(mode="json")
                screening_document = planned_row.plan.merge_plan.get("screening")
                screening_evaluation = (
                    ScreeningEvaluation.model_validate(screening_document)
                    if screening_document is not None
                    else None
                )
                change_summary = ChangeSummary.model_validate(
                    planned_row.plan.merge_plan["change_summary"]
                )
                row_plan_hash = self.hash_row(
                    context=context,
                    staged=planned_row.staging,
                    plan=planned_row.plan,
                    locator=planned_row.locator,
                    duplicate_owner=self.duplicate_owner_locator(planned_row.plan.merge_plan),
                    screening=screening_evaluation,
                    change_summary=change_summary,
                )
                batch_entries.append(
                    BatchPlanHashEntry(
                        locator=planned_row.locator,
                        row_plan_hash=row_plan_hash,
                    )
                )
                reconciled_rows.append(replace(planned_row, row_plan_hash=row_plan_hash))
            rows = reconciled_rows

        summary = summarize_preview(
            occurrence_count=len(files),
            included_file_count=len(included),
            excluded_file_count=len(files) - len(included),
            rows=tuple(facts),
        )
        summary_document = summary.model_dump(mode="json")
        summary_document.update(
            {
                "schema_version": 1,
                "context_hash": context.context_hash,
                "batch_plan_hash": hash_batch_plan(context.context_hash, tuple(batch_entries)),
                "manifest": [item.model_dump(mode="json") for item in context.files],
                "screening_rule_snapshot": {
                    **screening.model_dump(mode="json"),
                    "rule_hash": screening.rule_hash,
                },
            }
        )
        if refresh_return is not None:
            summary_document["refresh_return"] = {
                **refresh_return.summary.model_dump(mode="json"),
                "missing_queue_item_ids": [
                    str(item.queue_item_id)
                    for item in refresh_return.item_evidence
                    if item.matching_row_count == 0
                ],
            }
        return UnifiedPlanResult(
            context=context,
            screening=screening,
            rows=tuple(rows),
            prefetched_state=prefetched_state,
            summary=summary_document,
            refresh_return=refresh_return,
        )

    async def refresh_return_preview(
        self,
        job: ImportJob,
        rows: tuple[UnifiedPlanRow, ...],
        *,
        for_update: bool,
    ) -> RefreshReturnPreview | None:
        """Build linked Queue evidence strictly after existing Hard Match planning."""

        if job.refresh_queue_id is None:
            return None
        queue = await self.refresh_queue_repository.get_queue(
            job.refresh_queue_id,
            department_id=job.department_id,
            for_update=for_update,
        )
        if queue is None:
            raise ImportDomainError(
                "REFRESH_QUEUE_NOT_FOUND",
                "Refresh queue not found",
                status_code=404,
            )
        if queue.status not in {RefreshQueueStatus.OPEN, RefreshQueueStatus.EXPORTED}:
            raise ImportDomainError(
                "REFRESH_QUEUE_NOT_RETURNABLE",
                "Refresh queue cannot accept returns in its current status",
                status_code=409,
            )
        queue_items = await self.refresh_queue_repository.list_reconciliation_items(
            queue.id,
            for_update=for_update,
        )
        return reconcile_refresh_return(
            (
                RefreshReturnRow(
                    locator=row.locator,
                    source=row.staging.record.source,
                    action=row.plan.action,
                    matched_platform_account_id=row.plan.matched_platform_account_id,
                    is_owner_effective=not bool(row.plan.merge_plan.get("batch_duplicate")),
                    source_acquired_at=_as_utc(row.staging.occurrence.source_acquired_at),
                    source_acquired_at_confirmation_required=(
                        row.staging.occurrence.source_acquired_at_confirmation_required
                    ),
                    change_summary=ChangeSummary.model_validate(
                        row.plan.merge_plan["change_summary"]
                    ),
                )
                for row in rows
            ),
            queue_items,
        )

    @staticmethod
    def hash_row(
        *,
        context: PreviewRevisionContext,
        staged: _StagingRow,
        plan: PlannedImportRow,
        locator: PreviewRowLocator,
        duplicate_owner: PreviewRowLocator | None,
        screening: ScreeningEvaluation | None,
        change_summary: ChangeSummary,
    ) -> str:
        return hash_preview_row(
            PreviewRowHashInput(
                context_hash=context.context_hash,
                locator=locator,
                normalized_data=staged.normalized_data,
                action=plan.action,
                match_type=plan.match_type,
                matched_influencer_id=plan.matched_influencer_id,
                matched_platform_account_id=plan.matched_platform_account_id,
                duplicate_owner_locator=duplicate_owner,
                merge_plan=plan.merge_plan,
                preconditions=plan.preconditions,
                screening=screening,
                change_summary=change_summary,
                warnings=tuple(plan.warnings),
                errors=tuple(plan.errors),
                manual_review=plan.merge_plan.get("batch_manual_review"),
            )
        )

    @staticmethod
    def screening_snapshot(collection: CollectionJob) -> ScreeningRuleSnapshot:
        return ScreeningRuleSnapshot(
            rules=ScreeningRulePayload.model_validate(collection.screening_rules),
            rule_revision=collection.screening_rules_revision,
            follower_min=collection.follower_min,
            follower_max=collection.follower_max,
        )

    async def manifest(
        self,
        job: ImportJob,
        files: list[ImportJobFile],
    ) -> tuple[PreviewFileManifestEntry, ...]:
        file_records = {
            record.occurrence.id: record
            for record in await self.repository.list_import_job_file_records(job.id)
        }
        if set(file_records) != {item.id for item in files}:
            raise ImportDomainError(
                "IMPORT_PREVIEW_REVALIDATION_FAILED",
                "Preview file lineage changed during revalidation",
            )
        manifest: list[PreviewFileManifestEntry] = []
        for item in files:
            record = file_records[item.id]
            if record.stored_file.id != item.stored_file_id:
                raise ImportDomainError(
                    "IMPORT_PREVIEW_REVALIDATION_FAILED",
                    "Preview file lineage changed during revalidation",
                )
            if item.status is ImportJobFileStatus.READY and (
                not item.field_mapping
                or item.mapping_hash != hash_document(dict(item.field_mapping))
            ):
                raise ImportDomainError(
                    "IMPORT_PREVIEW_REVALIDATION_FAILED",
                    "Preview file mapping changed during revalidation",
                )
            manifest.append(
                PreviewFileManifestEntry(
                    import_job_file_id=item.id,
                    position=item.position,
                    stored_file_sha256=record.stored_file.sha256,
                    mapping_hash=item.mapping_hash,
                    status=item.status,
                    included=item.status is ImportJobFileStatus.READY,
                    source_acquired_at=_as_utc(item.source_acquired_at),
                    source_acquired_at_origin=item.source_acquired_at_origin,
                    source_acquired_at_confirmation_required=(
                        item.source_acquired_at_confirmation_required
                    ),
                )
            )
        return tuple(manifest)

    @staticmethod
    def plan_has_possible_duplicate_contact(merge_plan: dict[str, Any]) -> bool:
        contacts = merge_plan.get("contacts") or {}
        return bool(contacts.get("mark_duplicate_ids")) or any(
            item.get("possible_duplicate_contact") is True
            for item in contacts.get("create", [])
            if isinstance(item, dict)
        )

    @staticmethod
    def duplicate_owner_locator(
        merge_plan: dict[str, Any],
    ) -> PreviewRowLocator | None:
        duplicate = merge_plan.get("batch_duplicate")
        if not isinstance(duplicate, dict):
            return None
        try:
            return PreviewRowLocator(
                import_job_file_id=UUID(duplicate["owner_import_job_file_id"]),
                file_position=duplicate["owner_file_position"],
                row_number=duplicate["owner_row_number"],
                import_row_id=UUID(duplicate["owner_import_row_id"]),
            )
        except (KeyError, TypeError, ValueError):
            raise ImportDomainError(
                "IMPORT_STAGING_INVALID", "Batch duplicate owner evidence is invalid"
            ) from None


__all__ = [
    "RevalidationMode",
    "UnifiedPlanBuilder",
    "UnifiedPlanResult",
    "UnifiedPlanRow",
]
