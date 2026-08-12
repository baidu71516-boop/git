"""Worker-side builder for one immutable Phase 2 Unified Preview revision."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
from backend_core.imports.batch_processor import BatchImportProcessor
from backend_core.imports.bulk_repository import AccountSourceKey
from backend_core.imports.enums import (
    ImportJobFailedStage,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportRowAction,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.hashing import hash_document
from backend_core.imports.models import CollectionJob, ImportJob, ImportJobFile, ImportRow
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.planner import identity_lock_keys
from backend_core.imports.preview_domain import (
    BatchPlanHashEntry,
    PreviewFileManifestEntry,
    PreviewRevisionContext,
    PreviewRowFact,
    PreviewRowHashInput,
    PreviewRowLocator,
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


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class UnifiedPreviewProcessor:
    """Rebuild Task 3 staging into one deterministic, persisted revision."""

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
        self.audit = AuditRepository(session)
        self.batch = BatchImportProcessor(
            session,
            storage,
            parser_limits=parser_limits,
            max_batch_rows=max_batch_rows,
        )
        self.max_batch_rows = max_batch_rows

    async def build(self, import_job_id: UUID, task_id: str) -> dict[str, Any]:
        """Build once for the current persisted token; stale/replayed tasks are no-ops."""

        try:
            await self.repository.acquire_identity_locks(
                [f"phase2:unified-preview-job:{import_job_id}"]
            )
            # Collection mutations lock CollectionJob before invalidating its
            # ImportJobs.  Preview follows the same row-lock order: discover the
            # immutable FK without a lock, then CollectionJob -> ImportJob ->
            # ordered occurrences.  This prevents a screening-update deadlock.
            discovered_job = await self.repository.get_import_job(import_job_id)
            if discovered_job is None:
                raise ImportDomainError(
                    "IMPORT_JOB_NOT_FOUND", "Import job not found", status_code=404
                )
            if discovered_job.stored_file_id is not None:
                raise ImportDomainError(
                    "INVALID_IMPORT_MODE",
                    "Unified Preview requires a bulk import job",
                    status_code=409,
                )
            collection = await self.repository.get_collection_job(
                discovered_job.collection_job_id, for_update=True
            )
            if collection is None:
                raise ImportDomainError(
                    "COLLECTION_JOB_NOT_FOUND", "Collection job not found", status_code=404
                )
            job = await self.repository.get_import_job(import_job_id, for_update=True)
            if job is None or job.collection_job_id != collection.id:
                raise ImportDomainError(
                    "IMPORT_PREVIEW_REVALIDATION_FAILED",
                    "Preview job context changed during revalidation",
                )
            if (
                job.status is ImportJobStatus.PREVIEW_READY
                and job.parse_task_id == task_id
                and job.preview_revision > 0
            ):
                result = self._result(job, idempotent=True)
                await self.session.rollback()
                return result
            if job.status is not ImportJobStatus.PREVIEWING or job.parse_task_id != task_id:
                result = self._result(job, idempotent=True)
                await self.session.rollback()
                return result

            files = await self.repository.list_import_job_files(job.id, for_update=True)
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
            if any(item.source_acquired_at_confirmation_required for item in included):
                raise ImportDomainError(
                    "SOURCE_ACQUIRED_AT_CONFIRMATION_REQUIRED",
                    "Confirm source acquisition time before generating Preview",
                    status_code=409,
                )
            screening = self._screening_snapshot(collection)
            manifest = await self._manifest(job, files)
            next_revision = job.preview_revision + 1
            context = PreviewRevisionContext(
                import_job_id=job.id,
                preview_revision=next_revision,
                collection_job_id=job.collection_job_id,
                source_type=job.source_type,
                files=manifest,
                screening=screening,
                planner_version="phase1b-v1",
                relevant_config={"max_batch_rows": self.max_batch_rows},
            )

            # Unified Preview never trusts revision-zero staging as input.  It
            # re-verifies size/SHA and safely parses/adapts every included blob.
            staging_rows = await self.batch.revalidate_ready_files(job, files)
            if job.status is not ImportJobStatus.PREVIEWING or job.parse_task_id != task_id:
                result = self._result(job, idempotent=True)
                await self.session.rollback()
                return result

            # Re-read the frozen facts after storage I/O.  PostgreSQL row locks
            # prevent legitimate writers; these comparisons additionally make
            # unexpected mutation/corruption fail closed before planning.
            await self.session.refresh(collection)
            await self.session.refresh(job)
            for item in files:
                await self.session.refresh(item)
            if job.status is not ImportJobStatus.PREVIEWING or job.parse_task_id != task_id:
                result = self._result(job, idempotent=True)
                await self.session.rollback()
                return result
            if self._screening_snapshot(collection) != screening:
                raise ImportDomainError(
                    "IMPORT_PREVIEW_REVALIDATION_FAILED",
                    "Screening rules changed during Preview revalidation",
                )
            if await self._manifest(job, files) != manifest:
                raise ImportDomainError(
                    "IMPORT_PREVIEW_REVALIDATION_FAILED",
                    "Preview file manifest changed during revalidation",
                )

            # All identity advisory locks are acquired before the first Task 4
            # prefetch/Planner database read.
            await self.repository.acquire_identity_locks(
                key
                for staged in staging_rows
                if not staged.initial_errors
                for key in identity_lock_keys(staged.record)
            )
            plans, prefetched_state = await self.batch.plan_preview_staging(
                job,
                staging_rows,
                preview_revision=next_revision,
            )
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
                    from backend_core.imports.preview_domain import ChangeSummary

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
                if plan.action is ImportRowAction.UPDATE and not has_effective_changes(
                    change_summary
                ):
                    plan = replace(plan, action=ImportRowAction.NO_CHANGE)
                plan.merge_plan["change_summary"] = change_summary.model_dump(mode="json")
                plan.merge_plan["preview_context_hash"] = context.context_hash
                locator = PreviewRowLocator(
                    import_job_file_id=staged.occurrence.id,
                    file_position=staged.occurrence.position,
                    row_number=staged.row_number,
                    import_row_id=staged.import_row_id,
                )
                duplicate_owner = self._duplicate_owner_locator(plan.merge_plan)
                plan_hash = hash_preview_row(
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
                        screening=screening_evaluation,
                        change_summary=change_summary,
                        warnings=tuple(plan.warnings),
                        errors=tuple(plan.errors),
                        manual_review=plan.merge_plan.get("batch_manual_review"),
                    )
                )
                model = staged.model
                if model is None:
                    model = ImportRow(
                        id=staged.import_row_id,
                        import_job_id=job.id,
                        import_job_file_id=staged.occurrence.id,
                        row_number=staged.row_number,
                    )
                    self.session.add(model)
                model.raw_data = staged.raw_data
                model.normalized_data = staged.normalized_data
                model.matched_influencer_id = plan.matched_influencer_id
                model.matched_platform_account_id = plan.matched_platform_account_id
                model.match_type = plan.match_type
                model.action = plan.action
                model.merge_plan = plan.merge_plan
                model.warnings = plan.warnings
                model.errors = plan.errors
                model.preview_revision = next_revision
                model.plan_hash = plan_hash
                possible_duplicate = any(
                    warning.get("code") == "POSSIBLE_DUPLICATE_CONTACT" for warning in plan.warnings
                ) or self._plan_has_possible_duplicate_contact(plan.merge_plan)
                facts.append(
                    PreviewRowFact(
                        action=plan.action,
                        has_warning=bool(plan.warnings),
                        is_batch_duplicate=is_duplicate,
                        possible_duplicate_contact=possible_duplicate,
                        screening_result=(
                            screening_evaluation.result
                            if screening_evaluation is not None
                            else None
                        ),
                    )
                )
                batch_entries.append(BatchPlanHashEntry(locator=locator, row_plan_hash=plan_hash))

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
            job.preview_revision = next_revision
            job.preview_summary = summary_document
            job.status = ImportJobStatus.PREVIEW_READY
            job.failed_stage = None
            job.error_code = None
            job.error_message = None
            self._project_legacy_counts(job, summary_document)
            self.audit.add(
                action=AuditAction.IMPORT_BATCH_PREVIEW_CREATED,
                result=AuditResult.SUCCESS,
                department_id=job.department_id,
                operator_id=job.operator_id,
                ip="worker",
                user_agent="imports.preview_import_job",
                entity_type="import_job",
                entity_id=job.id,
                after={
                    "preview_revision": next_revision,
                    "batch_plan_hash": summary_document["batch_plan_hash"],
                },
            )
            await self.session.commit()
            return self._result(job, idempotent=False)
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                await self.session.rollback()
                raise
            await self.session.rollback()
            await self._mark_failed(import_job_id, task_id, exc)
            if isinstance(exc, ImportDomainError):
                raise
            raise ImportDomainError(
                "IMPORT_PREVIEW_FAILED", "Unified Preview failed safely"
            ) from exc

    @staticmethod
    def _screening_snapshot(collection: CollectionJob) -> ScreeningRuleSnapshot:
        return ScreeningRuleSnapshot(
            rules=ScreeningRulePayload.model_validate(collection.screening_rules),
            rule_revision=collection.screening_rules_revision,
            follower_min=collection.follower_min,
            follower_max=collection.follower_max,
        )

    async def _manifest(
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

    async def mark_failed_after_retry_exhausted(
        self,
        import_job_id: UUID,
        task_id: str,
    ) -> None:
        """Persist one safe terminal failure only for the still-current token."""

        await self.session.rollback()
        await self._mark_failed(
            import_job_id,
            task_id,
            ImportDomainError(
                "HEAVY_IMPORT_RETRY_EXHAUSTED",
                "Unified Preview could not acquire the heavy-import slot",
            ),
        )

    @staticmethod
    def _project_legacy_counts(job: ImportJob, summary: dict[str, Any]) -> None:
        job.total_rows = summary["raw_rows"]
        job.valid_rows = summary["raw_rows"] - summary["error_rows"]
        job.warning_rows = summary["warning_rows"]
        job.error_rows = summary["error_rows"]
        job.created_rows = summary["created_rows"]
        job.updated_rows = summary["updated_rows"]
        job.no_change_rows = summary["no_change_rows"]
        job.skipped_rows = summary["skipped_rows"]
        job.manual_review_rows = summary["manual_review_rows"]

    @staticmethod
    def _plan_has_possible_duplicate_contact(merge_plan: dict[str, Any]) -> bool:
        contacts = merge_plan.get("contacts") or {}
        return bool(contacts.get("mark_duplicate_ids")) or any(
            item.get("possible_duplicate_contact") is True
            for item in contacts.get("create", [])
            if isinstance(item, dict)
        )

    @staticmethod
    def _duplicate_owner_locator(
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
        except (KeyError, TypeError, ValueError) as exc:
            raise ImportDomainError(
                "IMPORT_STAGING_INVALID", "Batch duplicate owner evidence is invalid"
            ) from exc

    async def _mark_failed(self, import_job_id: UUID, task_id: str, exc: BaseException) -> None:
        job = await self.repository.get_import_job(import_job_id, for_update=True)
        if (
            job is None
            or job.status is not ImportJobStatus.PREVIEWING
            or job.parse_task_id != task_id
            or job.stored_file_id is not None
        ):
            await self.session.rollback()
            return
        job.status = ImportJobStatus.FAILED
        job.failed_stage = ImportJobFailedStage.PREVIEW
        job.error_code = exc.code if isinstance(exc, ImportDomainError) else "IMPORT_PREVIEW_FAILED"
        job.error_message = (
            exc.message if isinstance(exc, ImportDomainError) else "Unified Preview failed safely"
        )
        self.audit.add(
            action=AuditAction.IMPORT_BATCH_FAILED,
            result=AuditResult.FAILED,
            department_id=job.department_id,
            operator_id=job.operator_id,
            ip="worker",
            user_agent="imports.preview_import_job",
            entity_type="import_job",
            entity_id=job.id,
            after={"failed_stage": ImportJobFailedStage.PREVIEW.value},
        )
        await self.session.commit()

    @staticmethod
    def _result(job: ImportJob, *, idempotent: bool) -> dict[str, Any]:
        return {
            "import_job_id": str(job.id),
            "status": job.status.value,
            "preview_revision": job.preview_revision,
            "idempotent": idempotent,
        }


__all__ = ["UnifiedPreviewProcessor"]
