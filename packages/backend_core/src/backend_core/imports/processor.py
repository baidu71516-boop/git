"""Atomic worker-side parsing, preview planning, validation, and commit."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
from backend_core.config.settings import Settings
from backend_core.imports.adapters import (
    GenericCsvAdapter,
    HuitunCsvAdapter,
    HuitunExcelAdapter,
)
from backend_core.imports.contracts import AdaptedRow, CanonicalInfluencerRecord, SourceAdapter
from backend_core.imports.enums import (
    ImportJobFailedStage,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportRowAction,
    ImportSourceType,
    StoredFileType,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.hashing import hash_document
from backend_core.imports.mappings import HUITUN_FIELD_MAPPING
from backend_core.imports.merge_applier import ImportMergeApplier, PreviewStaleError
from backend_core.imports.models import ImportJob, ImportRow
from backend_core.imports.parsers import ParsedTable, ParserLimits, parse_table
from backend_core.imports.planner import (
    ImportPlanner,
    PlannedImportRow,
    build_preview_context,
    identity_lock_keys,
)
from backend_core.imports.repository import ImportRepository
from backend_core.imports.state_machine import transition_import_job
from backend_core.imports.storage import StorageAdapter
from backend_core.imports.task_service import ImportTaskService


class ImportProcessor:
    """The only implementation of worker-side Phase 1B business rules."""

    def __init__(
        self,
        session: AsyncSession,
        storage: StorageAdapter,
        *,
        parser_limits: ParserLimits,
        task_settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.repository = ImportRepository(session)
        self.merge_applier = ImportMergeApplier(session, repository=self.repository)
        self.audit = AuditRepository(session)
        self.parser_limits = parser_limits
        self.task_service = (
            ImportTaskService(session, task_settings) if task_settings is not None else None
        )

    async def parse_and_preview(
        self,
        import_job_id: UUID,
        *,
        task_token: UUID | None = None,
        task_generation: int | None = None,
    ) -> dict[str, Any]:
        task_context = self._task_context(task_token, task_generation)
        try:
            job = await self.repository.get_import_job(import_job_id, for_update=True)
            if job is None:
                raise ImportDomainError(
                    "IMPORT_JOB_NOT_FOUND", "Import job not found", status_code=404
                )
            if job.status == ImportJobStatus.CANCELLED:
                await self.session.rollback()
                return {"import_job_id": str(job.id), "status": job.status.value}
            if job.status == ImportJobStatus.UPLOADED:
                transition_import_job(job, ImportJobStatus.PARSING)
            elif job.status not in {
                ImportJobStatus.PARSING,
                ImportJobStatus.PREVIEWING,
            }:
                result = {
                    "import_job_id": str(job.id),
                    "status": job.status.value,
                    "preview_revision": job.preview_revision,
                }
                await self.session.rollback()
                return result
            occurrence = await self.repository.get_legacy_import_job_file(job, for_update=True)
            occurrence.status = ImportJobFileStatus.PARSING
            occurrence.parse_task_id = job.parse_task_id
            occurrence.error_code = None
            occurrence.error_message = None
            stored_file = await self.repository.get_stored_file(occurrence.stored_file_id)
            if stored_file is None:
                raise ImportDomainError("FILE_NOT_FOUND", "Stored import file not found")
            await self.session.commit()

            content = await self.storage.read(
                stored_file.storage_key,
                expected_size=stored_file.size,
                expected_sha256=stored_file.sha256,
            )
            table = parse_table(
                content,
                file_type=stored_file.detected_type,
                declared_mime=occurrence.declared_mime or job.mime_type or "",
                limits=self.parser_limits,
            )
            mapping = dict(occurrence.field_mapping or job.field_mapping or {})
            adapter: SourceAdapter | None = None
            if mapping:
                adapter = self._adapter(job.source_type, table.file_type, mapping)
                mapping = adapter.mapping_for_headers(table.headers)
            elif job.source_type == ImportSourceType.MANUAL_HUITUN_EXPORT:
                candidate = self._adapter(job.source_type, table.file_type, None)
                try:
                    mapping = candidate.mapping_for_headers(table.headers)
                    adapter = candidate
                except ImportDomainError as exc:
                    if exc.code != "MAPPING_INVALID":
                        raise
                    mapping = {
                        source: target
                        for source, target in HUITUN_FIELD_MAPPING.items()
                        if source in table.headers
                    }

            if adapter is None:
                await self._mark_mapping_required(
                    job.id,
                    occurrence.id,
                    table,
                    mapping,
                    task_context=task_context,
                )
                return {
                    "import_job_id": str(job.id),
                    "status": ImportJobStatus.MAPPING_REQUIRED.value,
                }

            adapted_rows = [adapter.adapt(raw_row) for raw_row in table.rows]
            return await self._persist_preview(
                job.id,
                occurrence.id,
                table,
                mapping,
                adapted_rows,
                task_context=task_context,
            )
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            await self.session.rollback()
            if task_context is not None:
                raise
            await self._mark_failed(import_job_id, exc, mark_file_failed=True)
            if isinstance(exc, ImportDomainError):
                raise
            raise ImportDomainError(
                "IMPORT_PROCESSING_FAILED", "Import processing failed safely"
            ) from None

    def _adapter(
        self,
        source_type: ImportSourceType,
        file_type: StoredFileType,
        mapping: dict[str, str] | None,
    ) -> SourceAdapter:
        if source_type == ImportSourceType.MANUAL_HUITUN_EXPORT:
            adapter_type = (
                HuitunCsvAdapter if file_type == StoredFileType.CSV else HuitunExcelAdapter
            )
            return adapter_type(mapping)
        if source_type == ImportSourceType.GENERIC_CSV:
            if file_type != StoredFileType.CSV:
                raise ImportDomainError(
                    "UNSUPPORTED_SOURCE_FILE_COMBINATION",
                    "Generic source currently supports CSV only",
                )
            if not mapping:
                raise ImportDomainError("MAPPING_REQUIRED", "Generic CSV requires mapping")
            return GenericCsvAdapter(mapping)
        raise ImportDomainError("UNSUPPORTED_SOURCE", "Unsupported import source")

    async def _mark_mapping_required(
        self,
        job_id: UUID,
        import_job_file_id: UUID,
        table: ParsedTable,
        mapping: dict[str, str],
        *,
        task_context: tuple[UUID, int] | None = None,
    ) -> None:
        job = await self.repository.get_import_job(job_id, for_update=True)
        occurrence = await self.repository.get_import_job_file(import_job_file_id, for_update=True)
        if job is None or occurrence is None or occurrence.import_job_id != job.id:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found")
        stored_file = await self.repository.get_stored_file(occurrence.stored_file_id)
        if stored_file is None:
            raise ImportDomainError("FILE_NOT_FOUND", "Stored import file not found")
        if job.status == ImportJobStatus.CANCELLED:
            await self.session.rollback()
            return
        if job.status == ImportJobStatus.MAPPING_REQUIRED:
            await self.session.rollback()
            return
        if job.status != ImportJobStatus.PARSING:
            raise ImportDomainError(
                "INVALID_STATE_TRANSITION", "Mapping cannot be requested in this state"
            )
        job.detected_fields = table.headers
        job.field_mapping = mapping or None
        job.mapping_hash = hash_document(mapping) if mapping else None
        occurrence.detected_fields = list(table.headers)
        occurrence.field_mapping = mapping or None
        occurrence.mapping_hash = job.mapping_hash
        occurrence.raw_rows = len(table.rows)
        occurrence.warning_rows = int(bool(table.warnings))
        occurrence.error_rows = 0
        occurrence.status = ImportJobFileStatus.MAPPING_REQUIRED
        stored_file.encoding = table.encoding
        stored_file.parse_metadata = {
            "delimiter": table.delimiter,
            "header_count": len(table.headers),
        }
        transition_import_job(job, ImportJobStatus.MAPPING_REQUIRED)
        await self._complete_task(task_context)
        await self.session.commit()

    async def _persist_preview(
        self,
        job_id: UUID,
        import_job_file_id: UUID,
        table: ParsedTable,
        mapping: dict[str, str],
        adapted_rows: list[AdaptedRow],
        *,
        task_context: tuple[UUID, int] | None = None,
    ) -> dict[str, Any]:
        job = await self.repository.get_import_job(job_id, for_update=True)
        occurrence = await self.repository.get_import_job_file(import_job_file_id, for_update=True)
        if job is None or occurrence is None or occurrence.import_job_id != job.id:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found")
        stored_file = await self.repository.get_stored_file(occurrence.stored_file_id)
        if stored_file is None:
            raise ImportDomainError("FILE_NOT_FOUND", "Stored import file not found")
        if job.status == ImportJobStatus.CANCELLED:
            await self.session.rollback()
            return {"import_job_id": str(job_id), "status": job.status.value}
        if job.status == ImportJobStatus.PREVIEW_READY:
            result = {
                "import_job_id": str(job.id),
                "status": job.status.value,
                "preview_revision": job.preview_revision,
            }
            await self.session.rollback()
            return result
        if job.status in {
            ImportJobStatus.FAILED,
            ImportJobStatus.PREVIEW_STALE,
            ImportJobStatus.CONFIRM_QUEUED,
            ImportJobStatus.IMPORTING,
            ImportJobStatus.COMPLETED,
        }:
            result = {"import_job_id": str(job.id), "status": job.status.value}
            await self.session.rollback()
            return result
        if job.status == ImportJobStatus.PARSING:
            transition_import_job(job, ImportJobStatus.PREVIEWING)
        elif job.status != ImportJobStatus.PREVIEWING:
            raise ImportDomainError(
                "INVALID_STATE_TRANSITION", "Preview cannot be persisted in this state"
            )
        preview_revision = job.preview_revision + 1
        mapping_hash = hash_document(mapping)
        valid_records = [(row.row_number, row.record) for row in adapted_rows if row.is_valid]
        duplicate_owners, duplicate_emails = build_preview_context(valid_records)
        planner = ImportPlanner(self.repository)
        existing_rows = {
            row.row_number: row for row in await self.repository.all_import_rows(job.id)
        }
        plans: list[tuple[AdaptedRow, PlannedImportRow]] = []
        for index, adapted in enumerate(adapted_rows):
            initial_warnings = adapted.warning_dicts()
            if index == 0:
                initial_warnings.extend(table.warnings)
            plan = await planner.plan(
                job_id=job.id,
                row_number=adapted.row_number,
                record=adapted.record,
                normalized_data=adapted.normalized_data(),
                mapping_hash=mapping_hash,
                preview_revision=preview_revision,
                initial_warnings=initial_warnings,
                initial_errors=adapted.error_dicts(),
                duplicate_owner_row=duplicate_owners.get(adapted.row_number),
                possible_duplicate_emails=duplicate_emails,
            )
            plans.append((adapted, plan))
            row = existing_rows.get(adapted.row_number)
            if row is None:
                row = ImportRow(
                    import_job_id=job.id,
                    import_job_file_id=occurrence.id,
                    row_number=adapted.row_number,
                )
                self.session.add(row)
            elif row.import_job_file_id != occurrence.id:
                raise ImportDomainError(
                    "IMPORT_ROW_FILE_MISMATCH",
                    "Import row belongs to a different file occurrence",
                )
            row.raw_data = adapted.raw_data
            row.normalized_data = adapted.normalized_data()
            row.matched_influencer_id = plan.matched_influencer_id
            row.matched_platform_account_id = plan.matched_platform_account_id
            row.match_type = plan.match_type
            row.action = plan.action
            row.merge_plan = plan.merge_plan
            row.warnings = plan.warnings
            row.errors = plan.errors
            row.preview_revision = preview_revision
            row.plan_hash = plan.plan_hash
            row.committed_action = None
            row.committed_at = None

        summary = self._preview_summary(table, plans, duplicate_emails)
        job.detected_fields = table.headers
        job.field_mapping = mapping
        job.mapping_hash = mapping_hash
        job.preview_revision = preview_revision
        job.preview_summary = summary
        self._set_counts(job, summary)
        job.error_code = None
        job.error_message = None
        job.failed_stage = None
        occurrence.detected_fields = list(table.headers)
        occurrence.field_mapping = dict(mapping)
        occurrence.mapping_hash = mapping_hash
        occurrence.raw_rows = int(summary["total_rows"])
        occurrence.warning_rows = int(summary["warning_rows"])
        occurrence.error_rows = int(summary["error_rows"])
        occurrence.status = ImportJobFileStatus.READY
        occurrence.error_code = None
        occurrence.error_message = None
        stored_file.encoding = table.encoding
        stored_file.parse_metadata = {
            "delimiter": table.delimiter,
            "header_count": len(table.headers),
        }
        transition_import_job(job, ImportJobStatus.PREVIEW_READY)
        self.audit.add(
            action=(
                AuditAction.IMPORT_PREVIEW_CREATED
                if preview_revision == 1
                else AuditAction.IMPORT_PREVIEW_REGENERATED
            ),
            result=AuditResult.SUCCESS,
            department_id=job.department_id,
            operator_id=job.operator_id,
            ip="worker",
            user_agent="celery:parse_import_job",
            entity_type="import_job",
            entity_id=job.id,
            after={
                "preview_revision": preview_revision,
                "total_rows": summary["total_rows"],
                "created_rows": summary["created_rows"],
                "updated_rows": summary["updated_rows"],
                "error_rows": summary["error_rows"],
            },
        )
        await self._complete_task(task_context)
        await self.session.commit()
        return {
            "import_job_id": str(job.id),
            "status": job.status.value,
            "preview_revision": preview_revision,
        }

    @staticmethod
    def _preview_summary(
        table: ParsedTable,
        plans: list[tuple[AdaptedRow, PlannedImportRow]],
        duplicate_emails: set[str],
    ) -> dict[str, Any]:
        actions = [plan.action for _, plan in plans]
        valid_email_rows = sum(bool(row.record.contacts) for row, _ in plans)
        invalid_email_rows = sum(
            any(warning.code == "INVALID_EMAIL" for warning in row.warnings) for row, _ in plans
        )
        potential_duplicate_rows = sum(
            any(contact.normalized_value in duplicate_emails for contact in row.record.contacts)
            or bool(plan.merge_plan.get("contacts", {}).get("mark_duplicate_ids"))
            for row, plan in plans
        )
        return {
            "file_type": table.file_type.value,
            "field_count": len(table.headers),
            "total_rows": len(plans),
            "valid_rows": sum(not plan.errors for _, plan in plans),
            "warning_rows": sum(bool(plan.warnings) for _, plan in plans),
            "error_rows": actions.count(ImportRowAction.ERROR),
            "created_rows": actions.count(ImportRowAction.CREATE),
            "updated_rows": actions.count(ImportRowAction.UPDATE),
            "no_change_rows": actions.count(ImportRowAction.NO_CHANGE),
            "skipped_rows": actions.count(ImportRowAction.SKIP),
            "manual_review_rows": actions.count(ImportRowAction.MANUAL_REVIEW),
            "existing_rows": sum(
                action in {ImportRowAction.UPDATE, ImportRowAction.NO_CHANGE} for action in actions
            ),
            "valid_email_rows": valid_email_rows,
            "invalid_email_rows": invalid_email_rows,
            "missing_email_rows": len(plans) - valid_email_rows - invalid_email_rows,
            "possible_duplicate_contact_rows": potential_duplicate_rows,
        }

    @staticmethod
    def _set_counts(job: ImportJob, summary: dict[str, Any]) -> None:
        for field in (
            "total_rows",
            "valid_rows",
            "warning_rows",
            "error_rows",
            "created_rows",
            "updated_rows",
            "no_change_rows",
            "skipped_rows",
            "manual_review_rows",
        ):
            setattr(job, field, int(summary[field]))

    async def confirm(
        self,
        import_job_id: UUID,
        preview_revision: int,
        *,
        task_token: UUID | None = None,
        task_generation: int | None = None,
    ) -> dict[str, Any]:
        task_context = self._task_context(task_token, task_generation)
        try:
            early_result = await self._begin_importing(import_job_id, preview_revision)
            if early_result is not None:
                return early_result
            return await self._validate_and_commit(
                import_job_id,
                preview_revision,
                task_context=task_context,
            )
        except PreviewStaleError as exc:
            await self.session.rollback()
            await self._mark_stale(
                import_job_id,
                preview_revision,
                str(exc),
                task_context=task_context,
            )
            return {
                "import_job_id": str(import_job_id),
                "status": ImportJobStatus.PREVIEW_STALE.value,
                "error_code": "PREVIEW_STALE",
            }
        except IntegrityError:
            await self.session.rollback()
            await self._mark_stale(
                import_job_id,
                preview_revision,
                "database_identity_changed",
                task_context=task_context,
            )
            return {
                "import_job_id": str(import_job_id),
                "status": ImportJobStatus.PREVIEW_STALE.value,
                "error_code": "PREVIEW_STALE",
            }
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            await self.session.rollback()
            if task_context is not None:
                raise
            await self._mark_failed(import_job_id, exc, mark_file_failed=False)
            if isinstance(exc, ImportDomainError):
                raise
            raise ImportDomainError(
                "IMPORT_PROCESSING_FAILED", "Import processing failed safely"
            ) from None

    async def _begin_importing(
        self,
        import_job_id: UUID,
        preview_revision: int,
    ) -> dict[str, Any] | None:
        job = await self.repository.get_import_job(import_job_id, for_update=True)
        if job is None:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found")
        if job.status == ImportJobStatus.COMPLETED:
            if job.confirmed_revision != preview_revision:
                raise PreviewStaleError("completed_revision_mismatch")
            result = dict(job.result or {})
            await self.session.rollback()
            return result
        if job.status == ImportJobStatus.PREVIEW_STALE:
            result = {
                "import_job_id": str(job.id),
                "status": job.status.value,
                "error_code": "PREVIEW_STALE",
            }
            await self.session.rollback()
            return result
        if job.status in {ImportJobStatus.CANCELLED, ImportJobStatus.FAILED}:
            result = {"import_job_id": str(job.id), "status": job.status.value}
            await self.session.rollback()
            return result
        if job.confirmed_revision != preview_revision or job.preview_revision != preview_revision:
            raise PreviewStaleError("revision_mismatch")
        if job.status == ImportJobStatus.CONFIRM_QUEUED:
            transition_import_job(job, ImportJobStatus.IMPORTING)
            await self.session.commit()
            return None
        if job.status == ImportJobStatus.IMPORTING:
            await self.session.rollback()
            return None
        raise ImportDomainError(
            "INVALID_STATE_TRANSITION", "Import job is not queued for confirmation"
        )

    async def _validate_and_commit(
        self,
        import_job_id: UUID,
        preview_revision: int,
        *,
        task_context: tuple[UUID, int] | None = None,
    ) -> dict[str, Any]:
        job = await self.repository.get_import_job(import_job_id, for_update=True)
        if job is None:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found")
        if job.status == ImportJobStatus.COMPLETED:
            if job.confirmed_revision != preview_revision:
                raise PreviewStaleError("completed_revision_mismatch")
            return dict(job.result or {})
        if job.status == ImportJobStatus.PREVIEW_STALE:
            return {
                "import_job_id": str(job.id),
                "status": job.status.value,
                "error_code": "PREVIEW_STALE",
            }
        if job.status != ImportJobStatus.IMPORTING:
            raise ImportDomainError("INVALID_STATE_TRANSITION", "Import job is not importing")
        if job.preview_revision != preview_revision or job.confirmed_revision != preview_revision:
            raise PreviewStaleError("revision_mismatch")
        if not job.mapping_hash:
            raise PreviewStaleError("mapping_missing")
        rows = await self.repository.all_import_rows(job.id)
        records: list[tuple[int, CanonicalInfluencerRecord]] = []
        for row in rows:
            if row.preview_revision != preview_revision or row.normalized_data is None:
                raise PreviewStaleError("row_revision_mismatch")
            records.append(
                (row.row_number, CanonicalInfluencerRecord.model_validate(row.normalized_data))
            )
        duplicate_owners, duplicate_emails = build_preview_context(records)
        await self.repository.acquire_identity_locks(
            key for _, record in records for key in identity_lock_keys(record)
        )
        planner = ImportPlanner(self.repository)
        validated: list[tuple[ImportRow, CanonicalInfluencerRecord, PlannedImportRow]] = []
        for row, (_, record) in zip(rows, records, strict=True):
            plan = await planner.plan(
                job_id=job.id,
                row_number=row.row_number,
                record=record,
                normalized_data=row.normalized_data or {},
                mapping_hash=job.mapping_hash,
                preview_revision=preview_revision,
                initial_warnings=[issue.as_dict() for issue in record.warnings],
                initial_errors=[issue.as_dict() for issue in record.errors],
                duplicate_owner_row=duplicate_owners.get(row.row_number),
                possible_duplicate_emails=duplicate_emails,
            )
            if (
                plan.plan_hash != row.plan_hash
                or plan.action != row.action
                or plan.match_type != row.match_type
                or plan.matched_influencer_id != row.matched_influencer_id
                or plan.matched_platform_account_id != row.matched_platform_account_id
            ):
                raise PreviewStaleError(f"row_{row.row_number}_plan_changed")
            validated.append((row, record, plan))

        now = datetime.now(UTC)
        for row, record, plan in validated:
            await self._apply_plan(job, row, record, plan, now)
        result = {
            "import_job_id": str(job.id),
            "preview_revision": preview_revision,
            "created_rows": sum(plan.action == ImportRowAction.CREATE for _, _, plan in validated),
            "updated_rows": sum(plan.action == ImportRowAction.UPDATE for _, _, plan in validated),
            "no_change_rows": sum(
                plan.action == ImportRowAction.NO_CHANGE for _, _, plan in validated
            ),
            "skipped_rows": sum(plan.action == ImportRowAction.SKIP for _, _, plan in validated),
            "error_rows": sum(plan.action == ImportRowAction.ERROR for _, _, plan in validated),
            "manual_review_rows": sum(
                plan.action == ImportRowAction.MANUAL_REVIEW for _, _, plan in validated
            ),
        }
        job.result = result
        job.completed_at = now
        job.error_code = None
        job.error_message = None
        job.failed_stage = None
        transition_import_job(job, ImportJobStatus.COMPLETED)
        self.audit.add(
            action=AuditAction.IMPORT_CONFIRMED,
            result=AuditResult.SUCCESS,
            department_id=job.department_id,
            operator_id=job.operator_id,
            ip="worker",
            user_agent="celery:confirm_import_job",
            entity_type="import_job",
            entity_id=job.id,
            after={"preview_revision": preview_revision},
        )
        self.audit.add(
            action=AuditAction.IMPORT_COMPLETED,
            result=AuditResult.SUCCESS,
            department_id=job.department_id,
            operator_id=job.operator_id,
            ip="worker",
            user_agent="celery:confirm_import_job",
            entity_type="import_job",
            entity_id=job.id,
            after=result,
        )
        await self._complete_task(task_context)
        await self.session.commit()
        return result

    async def _apply_plan(
        self,
        job: ImportJob,
        row: ImportRow,
        record: CanonicalInfluencerRecord,
        plan: PlannedImportRow,
        now: datetime,
    ) -> None:
        await self.merge_applier.apply(job, row, record, plan, now)
        await self.merge_applier.finalize()

    async def _mark_stale(
        self,
        import_job_id: UUID,
        preview_revision: int,
        reason: str,
        *,
        task_context: tuple[UUID, int] | None = None,
    ) -> None:
        job = await self.repository.get_import_job(import_job_id, for_update=True)
        if job is None:
            await self.session.rollback()
            return
        if job.status in {ImportJobStatus.CONFIRM_QUEUED, ImportJobStatus.IMPORTING}:
            transition_import_job(job, ImportJobStatus.PREVIEW_STALE)
            job.error_code = "PREVIEW_STALE"
            job.error_message = "Preview-relevant data changed; regenerate the preview"
            self.audit.add(
                action=AuditAction.IMPORT_PREVIEW_STALE,
                result=AuditResult.DENIED,
                department_id=job.department_id,
                operator_id=job.operator_id,
                ip="worker",
                user_agent="celery:confirm_import_job",
                entity_type="import_job",
                entity_id=job.id,
                after={"preview_revision": preview_revision, "reason": reason[:120]},
            )
            if task_context is not None:
                assert self.task_service is not None
                token, generation = task_context
                if not await self.task_service.terminal_fail(token, generation=generation):
                    raise ImportDomainError(
                        "IMPORT_TASK_LEASE_LOST",
                        "Confirm lost its authoritative task lease before stale commit",
                    )
            await self.session.commit()
        else:
            await self.session.rollback()

    async def _mark_failed(
        self, import_job_id: UUID, exc: BaseException, *, mark_file_failed: bool
    ) -> None:
        job = await self.repository.get_import_job(import_job_id, for_update=True)
        if job is None:
            await self.session.rollback()
            return
        if job.status in {
            ImportJobStatus.COMPLETED,
            ImportJobStatus.CANCELLED,
            ImportJobStatus.PREVIEW_STALE,
            ImportJobStatus.FAILED,
        }:
            await self.session.rollback()
            return
        code = exc.code if isinstance(exc, ImportDomainError) else "IMPORT_PROCESSING_FAILED"
        message = (
            exc.message if isinstance(exc, ImportDomainError) else "Import processing failed safely"
        )
        if mark_file_failed:
            occurrences = await self.repository.list_import_job_files(job.id, for_update=True)
            if len(occurrences) == 1:
                occurrence = occurrences[0]
                occurrence.status = ImportJobFileStatus.FAILED
                occurrence.error_code = code[:80]
                occurrence.error_message = message
        job.failed_stage = (
            ImportJobFailedStage.PREVIEW if mark_file_failed else ImportJobFailedStage.CONFIRM
        )
        transition_import_job(job, ImportJobStatus.FAILED)
        job.error_code = code[:80]
        job.error_message = message
        self.audit.add(
            action=AuditAction.IMPORT_FAILED,
            result=AuditResult.FAILED,
            department_id=job.department_id,
            operator_id=job.operator_id,
            ip="worker",
            user_agent="celery:import_task",
            entity_type="import_job",
            entity_id=job.id,
            after={"error_code": code[:80]},
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
                "Import task lease was lost before the atomic business commit",
            )
