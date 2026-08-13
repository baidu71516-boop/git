"""Worker-side builder for one immutable Phase 2 Unified Preview revision."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
from backend_core.config.settings import Settings
from backend_core.imports.enums import (
    ImportJobFailedStage,
    ImportJobStatus,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.models import ImportJob, ImportRow
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.storage import StorageAdapter
from backend_core.imports.task_service import ImportTaskService
from backend_core.imports.unified_plan import RevalidationMode, UnifiedPlanBuilder


class UnifiedPreviewProcessor:
    """Rebuild Task 3 staging into one deterministic, persisted revision."""

    def __init__(
        self,
        session: AsyncSession,
        storage: StorageAdapter,
        *,
        parser_limits: ParserLimits,
        max_batch_rows: int = 10_000,
        task_settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.plan_builder = UnifiedPlanBuilder(
            session,
            storage,
            parser_limits=parser_limits,
            max_batch_rows=max_batch_rows,
        )
        # Keep the public seams used by the established lock-order and Planner
        # instrumentation tests. Both aliases point at the builder's instances.
        self.repository = self.plan_builder.repository
        self.audit = AuditRepository(session)
        self.batch = self.plan_builder.batch
        self.max_batch_rows = max_batch_rows
        self.task_service = (
            ImportTaskService(session, task_settings) if task_settings is not None else None
        )

    async def build(
        self,
        import_job_id: UUID,
        task_id: str,
        *,
        task_token: UUID | None = None,
        task_generation: int | None = None,
    ) -> dict[str, Any]:
        """Build once for the current persisted token; stale/replayed tasks are no-ops."""

        task_context = self._task_context(task_token, task_generation)
        try:
            task_id = str(UUID(task_id))
        except ValueError:
            # Invalid/stale broker payloads are compared as-is and therefore
            # converge on the existing guarded no-op path without touching DB
            # state. Persisted Task 6 tokens themselves are always UUIDs.
            pass
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
            next_revision = job.preview_revision + 1
            unified_plan = await self.plan_builder.build(
                collection,
                job,
                files,
                preview_revision=next_revision,
                revalidation_mode=RevalidationMode.PREVIEW_REPLACE,
            )
            if job.status is not ImportJobStatus.PREVIEWING or job.parse_task_id != task_id:
                result = self._result(job, idempotent=True)
                await self.session.rollback()
                return result

            for planned_row in unified_plan.rows:
                staged = planned_row.staging
                plan = planned_row.plan
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
                model.plan_hash = planned_row.row_plan_hash

            summary_document = unified_plan.summary
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
            await self._complete_task(task_context)
            await self.session.commit()
            return self._result(job, idempotent=False)
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                await self.session.rollback()
                raise
            await self.session.rollback()
            if task_context is not None:
                raise
            await self._mark_failed(import_job_id, task_id, exc)
            if isinstance(exc, ImportDomainError):
                raise
            raise ImportDomainError(
                "IMPORT_PREVIEW_FAILED", "Unified Preview failed safely"
            ) from None

    async def mark_failed_after_retry_exhausted(
        self,
        import_job_id: UUID,
        task_id: str,
    ) -> None:
        """Persist one safe terminal failure only for the still-current token."""

        try:
            task_id = str(UUID(task_id))
        except ValueError:
            pass
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
    def _task_context(
        task_token: UUID | None,
        task_generation: int | None,
    ) -> tuple[UUID, int] | None:
        if task_token is None and task_generation is None:
            return None
        if task_token is None or task_generation is None or task_generation < 1:
            raise ValueError("task_token and a positive task_generation must be provided together")
        return task_token, task_generation

    async def _complete_task(self, task_context: tuple[UUID, int] | None) -> None:
        if task_context is None:
            return
        assert self.task_service is not None
        task_token, generation = task_context
        if not await self.task_service.complete(task_token, generation):
            raise ImportDomainError(
                "IMPORT_TASK_LEASE_LOST",
                "Unified Preview lost its authoritative task lease before commit",
            )

    @staticmethod
    def _result(job: ImportJob, *, idempotent: bool) -> dict[str, Any]:
        return {
            "import_job_id": str(job.id),
            "status": job.status.value,
            "preview_revision": job.preview_revision,
            "idempotent": idempotent,
        }


__all__ = ["UnifiedPreviewProcessor"]
