"""Atomic Phase 2 Bulk Confirm using the frozen Unified Preview plan."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
from backend_core.config.settings import Settings
from backend_core.imports.enums import (
    ImportJobStatus,
    ImportTaskKind,
    ImportTaskState,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.hashing import canonical_json
from backend_core.imports.merge_applier import (
    ImportMergeApplier,
    MergeApplyCache,
    PreviewStaleError,
)
from backend_core.imports.models import ImportJob, ImportRow, ImportTaskRequest
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.repository import ImportRepository
from backend_core.imports.state_machine import transition_import_job
from backend_core.imports.storage import StorageAdapter
from backend_core.imports.task_service import ImportTaskService
from backend_core.imports.unified_plan import RevalidationMode, UnifiedPlanBuilder

logger = logging.getLogger(__name__)

_PREVIEW_REVALIDATION_STALE_CODES = frozenset(
    {
        "DUPLICATE_HEADER",
        "EMPTY_FILE",
        "FILE_COMPLEXITY_LIMIT",
        "FILE_INTEGRITY_FAILED",
        "FILE_NOT_FOUND",
        "IMPORT_BATCH_ROW_LIMIT",
        "IMPORT_PREVIEW_BLOCKED",
        "IMPORT_PREVIEW_EMPTY",
        "IMPORT_PREVIEW_REVALIDATION_FAILED",
        "INVALID_CSV",
        "INVALID_CSV_ENCODING",
        "INVALID_FILE_EXTENSION",
        "INVALID_FILE_TYPE",
        "INVALID_HEADER",
        "INVALID_STORAGE_KEY",
        "INVALID_XLSX",
        "MAPPING_INVALID",
        "MAPPING_REQUIRED",
        "MIME_MISMATCH",
        "NO_DATA_ROWS",
        "SOURCE_ACQUIRED_AT_CONFIRMATION_REQUIRED",
        "UNSAFE_XLSX",
        "UNSUPPORTED_SOURCE",
        "UNSUPPORTED_SOURCE_FILE_COMBINATION",
    }
)


class BulkConfirmProcessor:
    """Revalidate one exact revision and merge the complete batch in one commit."""

    def __init__(
        self,
        session: AsyncSession,
        storage: StorageAdapter,
        *,
        parser_limits: ParserLimits,
        settings: Settings,
        max_batch_rows: int = 10_000,
    ) -> None:
        self.session = session
        self.repository = ImportRepository(session)
        self.audit = AuditRepository(session)
        self.task_service = ImportTaskService(session, settings)
        self.plan_builder = UnifiedPlanBuilder(
            session,
            storage,
            parser_limits=parser_limits,
            max_batch_rows=max_batch_rows,
        )

    async def confirm(
        self,
        import_job_id: UUID,
        preview_revision: int,
        task_token: UUID | str,
        generation: int,
    ) -> dict[str, Any]:
        token = self._token(task_token)
        try:
            return await self._validate_and_commit(
                import_job_id,
                preview_revision,
                token,
                generation,
            )
        except PreviewStaleError as exc:
            # Never log exception repr here: database/parser exceptions may
            # carry SQL parameters or source-row values.  PreviewStaleError
            # reasons are deliberately short, deterministic identifiers.
            logger.info("bulk_confirm_revalidation_failed reason=%s", str(exc)[:120])
            await self.session.rollback()
            await self._mark_stale(
                import_job_id,
                preview_revision,
                token,
                generation,
                str(exc),
            )
            return {
                "import_job_id": str(import_job_id),
                "status": ImportJobStatus.PREVIEW_STALE.value,
                "error_code": "PREVIEW_STALE",
            }
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                await self.session.rollback()
                raise
            await self.session.rollback()
            if isinstance(exc, ImportDomainError):
                raise
            raise ImportDomainError("IMPORT_CONFIRM_FAILED", "Bulk Confirm failed safely") from None

    async def _validate_and_commit(
        self,
        import_job_id: UUID,
        preview_revision: int,
        task_token: UUID,
        generation: int,
    ) -> dict[str, Any]:
        # Use the same lock family and ordering as Unified Preview. Identity
        # locks are acquired later by the shared builder before its first
        # prefetch/Planner read.
        await self.repository.acquire_identity_locks(
            [f"phase2:unified-preview-job:{import_job_id}"]
        )
        discovered = await self.repository.get_import_job(import_job_id)
        if discovered is None:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found", status_code=404)
        if discovered.stored_file_id is not None:
            raise ImportDomainError(
                "INVALID_IMPORT_MODE", "Bulk Confirm requires a bulk import job", status_code=409
            )
        collection = await self.repository.get_collection_job(
            discovered.collection_job_id, for_update=True
        )
        if collection is None:
            raise PreviewStaleError("collection_deleted")
        job = await self.repository.get_import_job(import_job_id, for_update=True)
        if job is None or job.collection_job_id != collection.id:
            raise PreviewStaleError("job_context_changed")
        # Do not hold the task row lock while storage revalidation and the
        # batch transaction run: the independent heartbeat must be able to
        # renew this generation.  ``complete()`` performs the final atomic
        # generation/state/lease compare-and-set inside the business commit.
        task = await self.task_service.get_by_token(task_token)
        self._require_live_task(task, job, preview_revision, generation)
        if job.status is ImportJobStatus.COMPLETED:
            assert task is not None
            if task.state is not ImportTaskState.COMPLETED:
                raise PreviewStaleError("completed_task_mismatch")
            await self.session.rollback()
            return dict(job.result or {})
        if job.status not in {ImportJobStatus.CONFIRM_QUEUED, ImportJobStatus.IMPORTING}:
            raise PreviewStaleError("confirm_state_changed")
        if job.preview_revision != preview_revision or job.confirmed_revision != preview_revision:
            raise PreviewStaleError("revision_mismatch")
        if not isinstance(job.preview_summary, dict):
            raise PreviewStaleError("preview_summary_missing")
        frozen_summary = dict(job.preview_summary)
        if frozen_summary.get("schema_version") != 1:
            raise PreviewStaleError("preview_schema_version_changed")

        if job.status is ImportJobStatus.CONFIRM_QUEUED:
            transition_import_job(job, ImportJobStatus.IMPORTING)
        files = await self.repository.list_import_job_files(job.id, for_update=True)
        frozen_rows = await self.repository.all_import_rows(job.id)
        try:
            unified = await self.plan_builder.build(
                collection,
                job,
                files,
                preview_revision=preview_revision,
                revalidation_mode=RevalidationMode.CONFIRM_PRESERVE,
            )
        except ImportDomainError as exc:
            # Only deterministic frozen-input drift is a user-visible stale
            # Preview. Unknown Planner/cache invariants stay internal failures
            # instead of being disguised as a safe revalidation mismatch.
            if exc.code in _PREVIEW_REVALIDATION_STALE_CODES:
                raise PreviewStaleError(f"revalidation_{exc.code.lower()}") from None
            raise
        self._compare_frozen(frozen_summary, frozen_rows, unified)

        now = datetime.now(UTC)
        applier = ImportMergeApplier(
            self.session,
            cache=MergeApplyCache.from_prefetched(unified.prefetched_state),
        )
        for frozen, planned in zip(frozen_rows, unified.rows, strict=True):
            await applier.apply(
                job,
                frozen,
                planned.staging.record,
                planned.plan,
                now,
            )
        await applier.finalize()

        result = {
            "import_job_id": str(job.id),
            "preview_revision": preview_revision,
            "created_rows": unified.summary["created_rows"],
            "updated_rows": unified.summary["updated_rows"],
            "no_change_rows": unified.summary["no_change_rows"],
            "skipped_rows": unified.summary["skipped_rows"],
            "error_rows": unified.summary["error_rows"],
            "manual_review_rows": unified.summary["manual_review_rows"],
        }
        job.result = result
        job.completed_at = now
        job.error_code = None
        job.error_message = None
        job.failed_stage = None
        transition_import_job(job, ImportJobStatus.COMPLETED)
        if not await self.task_service.complete(task_token, generation):
            raise ImportDomainError(
                "IMPORT_TASK_LEASE_LOST",
                "Bulk Confirm lost its authoritative task lease before commit",
            )
        self.audit.add(
            action=AuditAction.IMPORT_BATCH_COMPLETED,
            result=AuditResult.SUCCESS,
            department_id=job.department_id,
            operator_id=job.operator_id,
            ip="worker",
            user_agent="imports.confirm_import_job",
            entity_type="import_job",
            entity_id=job.id,
            after={"preview_revision": preview_revision, **result},
        )
        await self.session.commit()
        return result

    @staticmethod
    def _compare_frozen(
        frozen_summary: dict[str, Any],
        frozen_rows: list[ImportRow],
        unified: Any,
    ) -> None:
        if canonical_json(frozen_summary) != canonical_json(unified.summary):
            raise PreviewStaleError("batch_plan_changed")
        if len(frozen_rows) != len(unified.rows):
            raise PreviewStaleError("row_set_changed")
        for frozen, planned in zip(frozen_rows, unified.rows, strict=True):
            staged = planned.staging
            plan = planned.plan
            if (
                frozen.id != planned.locator.import_row_id
                or frozen.import_job_file_id != planned.locator.import_job_file_id
                or frozen.row_number != planned.locator.row_number
                or frozen.preview_revision != unified.context.preview_revision
                or frozen.plan_hash != planned.row_plan_hash
                or canonical_json(frozen.raw_data) != canonical_json(staged.raw_data)
                or canonical_json(frozen.normalized_data) != canonical_json(staged.normalized_data)
                or frozen.action is not plan.action
                or frozen.match_type is not plan.match_type
                or frozen.matched_influencer_id != plan.matched_influencer_id
                or frozen.matched_platform_account_id != plan.matched_platform_account_id
                or canonical_json(frozen.merge_plan) != canonical_json(plan.merge_plan)
                or canonical_json(frozen.warnings) != canonical_json(plan.warnings)
                or canonical_json(frozen.errors) != canonical_json(plan.errors)
            ):
                raise PreviewStaleError(f"row_{frozen.id}_plan_changed")

    @staticmethod
    def _require_live_task(
        task: ImportTaskRequest | None,
        job: ImportJob,
        revision: int,
        generation: int,
    ) -> None:
        if task is None:
            raise ImportDomainError("IMPORT_TASK_NOT_FOUND", "Confirm task not found")
        if (
            task.task_kind is not ImportTaskKind.CONFIRM
            or task.import_job_id != job.id
            or task.preview_revision != revision
            or task.state is not ImportTaskState.RUNNING
            or task.run_attempts != generation
        ):
            raise ImportDomainError("IMPORT_TASK_MISMATCH", "Confirm task claim is no longer valid")

    async def _mark_stale(
        self,
        import_job_id: UUID,
        revision: int,
        token: UUID,
        generation: int,
        reason: str,
    ) -> None:
        logger.info(
            "bulk_confirm_preview_stale job_id=%s revision=%d reason=%s",
            import_job_id,
            revision,
            reason[:120],
        )
        job = await self.repository.get_import_job(import_job_id, for_update=True)
        if job is None:
            await self.session.rollback()
            return
        if job.status in {ImportJobStatus.CONFIRM_QUEUED, ImportJobStatus.IMPORTING}:
            transition_import_job(job, ImportJobStatus.PREVIEW_STALE)
            job.error_code = "PREVIEW_STALE"
            job.error_message = "Preview-relevant data changed; regenerate the preview"
            if not await self.task_service.terminal_fail(token, generation=generation):
                await self.session.rollback()
                raise ImportDomainError(
                    "IMPORT_TASK_LEASE_LOST",
                    "Bulk Confirm lost its authoritative task lease before stale settlement",
                )
            self.audit.add(
                action=AuditAction.IMPORT_PREVIEW_STALE,
                result=AuditResult.DENIED,
                department_id=job.department_id,
                operator_id=job.operator_id,
                ip="worker",
                user_agent="imports.confirm_import_job",
                entity_type="import_job",
                entity_id=job.id,
                after={"preview_revision": revision, "reason": reason[:120]},
            )
            await self.session.commit()
        else:
            await self.session.rollback()

    @staticmethod
    def _token(value: UUID | str) -> UUID:
        try:
            return value if isinstance(value, UUID) else UUID(value)
        except ValueError as exc:
            raise ImportDomainError("IMPORT_TASK_INVALID", "Confirm task token is invalid") from exc


__all__ = ["BulkConfirmProcessor"]
