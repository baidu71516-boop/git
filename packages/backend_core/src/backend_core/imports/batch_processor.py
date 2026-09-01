"""Worker-side Phase 2 parsing and revision-zero batch staging.

Task 3 deliberately stops before a unified Preview exists.  A successful file
parse therefore leaves the parent Job in ``draft`` and persists deterministic
``preview_revision == 0`` rows only.  Task 5 can later rebuild those staging
rows into the first immutable Preview revision.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.config.settings import Settings
from backend_core.imports.adapters import (
    GenericCsvAdapter,
    HuitunCsvAdapter,
    HuitunExcelAdapter,
)
from backend_core.imports.batch import (
    BatchRow,
    ComponentResolution,
    ComponentResolutionKind,
    DatabaseIdentityEvidence,
    DatabaseIdentityMatches,
    DatabaseIdentityTarget,
    IdentityComponent,
    ManualReviewReason,
    RowLocator,
    build_identity_graph,
    duplicate_email_values,
    resolve_identity_graph,
)
from backend_core.imports.bulk_repository import (
    BulkImportContext,
    BulkImportRepository,
    PrefetchedImportRepository,
    PrefetchedImportState,
)
from backend_core.imports.contracts import AdaptedRow, CanonicalInfluencerRecord, SourceAdapter
from backend_core.imports.enums import (
    ImportJobFileStatus,
    ImportJobStatus,
    ImportMatchType,
    ImportSourceType,
    StoredFileType,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.hashing import hash_document
from backend_core.imports.mappings import HUITUN_FIELD_MAPPING
from backend_core.imports.models import ImportJob, ImportJobFile, ImportRow, StoredImportFile
from backend_core.imports.parsers import ParsedTable, ParserLimits, parse_table
from backend_core.imports.planner import (
    ImportPlanner,
    MatchResult,
    PlannedImportRow,
    metric_snapshot_key,
)
from backend_core.imports.repository import ImportRepository
from backend_core.imports.storage import StorageAdapter
from backend_core.imports.task_service import ImportTaskService


@dataclass(frozen=True, slots=True)
class _TaskSnapshot:
    job_id: UUID
    file_id: UUID
    stored_file_id: UUID
    storage_key: str
    expected_size: int
    expected_sha256: str
    file_type: StoredFileType
    declared_mime: str
    source_type: ImportSourceType
    field_mapping: dict[str, str] | None
    task_id: str


@dataclass(slots=True)
class _StagingRow:
    occurrence: ImportJobFile
    import_row_id: UUID
    row_number: int
    raw_data: dict[str, Any]
    normalized_data: dict[str, Any]
    record: CanonicalInfluencerRecord
    mapping_hash: str
    initial_warnings: list[dict[str, Any]]
    initial_errors: list[dict[str, Any]]
    model: ImportRow | None

    @property
    def locator(self) -> RowLocator:
        return RowLocator(
            import_job_file_id=self.occurrence.id,
            file_position=self.occurrence.position,
            row_number=self.row_number,
            import_row_id=self.import_row_id,
        )


class RevalidationMode(StrEnum):
    """Controls whether rebuilt bytes may replace the persisted row cache."""

    PREVIEW_REPLACE = "preview_replace"
    CONFIRM_PRESERVE = "confirm_preserve"


class BatchImportProcessor:
    """Parse one occurrence and atomically rebuild the included batch graph."""

    def __init__(
        self,
        session: AsyncSession,
        storage: StorageAdapter,
        *,
        parser_limits: ParserLimits,
        max_batch_rows: int = 10_000,
        task_settings: Settings | None = None,
    ) -> None:
        if max_batch_rows < 1:
            raise ValueError("max_batch_rows must be positive")
        self.session = session
        self.storage = storage
        self.repository = ImportRepository(session)
        self.parser_limits = parser_limits
        self.max_batch_rows = max_batch_rows
        self.task_service = (
            ImportTaskService(session, task_settings) if task_settings is not None else None
        )

    async def parse_file(
        self,
        import_job_id: UUID,
        import_job_file_id: UUID,
        task_id: str,
        *,
        task_token: UUID | None = None,
        task_generation: int | None = None,
    ) -> dict[str, Any]:
        """Parse one persisted occurrence when its task token is still current."""

        task_context = self._task_context(task_token, task_generation)
        try:
            snapshot, early_result = await self._task_snapshot(
                import_job_id, import_job_file_id, task_id
            )
            if early_result is not None:
                return early_result
            assert snapshot is not None

            content = await self.storage.read(
                snapshot.storage_key,
                expected_size=snapshot.expected_size,
                expected_sha256=snapshot.expected_sha256,
            )
            table = parse_table(
                content,
                file_type=snapshot.file_type,
                declared_mime=snapshot.declared_mime,
                limits=self.parser_limits,
                repair_huitun_dimensions=(
                    snapshot.source_type is ImportSourceType.MANUAL_HUITUN_EXPORT
                ),
            )
            mapping, adapter = self._mapping_and_adapter(snapshot, table)
            if adapter is None:
                return await self._persist_mapping_required(
                    snapshot,
                    table,
                    mapping,
                    task_context=task_context,
                )

            adapter.validate_table(table.rows)
            adapted_rows = [adapter.adapt(raw_row) for raw_row in table.rows]
            return await self._persist_parsed_file(
                snapshot,
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
            await self._mark_failed(import_job_id, import_job_file_id, task_id, exc)
            if isinstance(exc, ImportDomainError):
                raise
            raise ImportDomainError(
                "IMPORT_PROCESSING_FAILED", "Import file processing failed safely"
            ) from None

    async def rebuild_ready_staging(
        self,
        import_job_id: UUID,
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        """Recompute current READY staging without reading another file.

        ``commit=False`` is intended for a Service mutation that already owns
        the Job transaction (notably exclude).  In that mode this method neither
        commits nor rolls back its caller's transaction.
        """

        job = await self.repository.get_import_job(import_job_id, for_update=True)
        self._require_bulk_draft(job)
        assert job is not None
        files = await self.repository.list_import_job_files(import_job_id, for_update=True)
        staged_rows = await self._persist_replanned_rows(job, files, replacement=None)
        if commit:
            await self.session.commit()
        return {
            "import_job_id": str(import_job_id),
            "status": ImportJobStatus.DRAFT.value,
            "staged_rows": staged_rows,
        }

    async def revalidate_ready_files(
        self,
        job: ImportJob,
        files: Sequence[ImportJobFile],
        *,
        mode: RevalidationMode,
    ) -> list[_StagingRow]:
        """Re-read and normalize every included READY occurrence.

        Task 3 staging is deliberately not trusted as the source of a Unified
        Preview.  This method verifies each immutable StoredImportFile, runs the
        same safe parser and source adapter again, and reconstructs rows from the
        bytes protected by the frozen per-file mapping.  Any row replacement or
        deletion remains in the caller's Preview transaction, so a later failure
        restores the last complete revision atomically.  Confirm uses
        ``CONFIRM_PRESERVE`` because revalidation must never prune the frozen
        Preview rows it is comparing against.
        """

        records = await self.repository.list_import_job_file_records(job.id)
        records_by_id = {record.occurrence.id: record for record in records}
        persisted_models = await self._all_job_rows(job.id)
        models_by_locator = {
            (model.import_job_file_id, model.row_number): model for model in persisted_models
        }
        rebuilt_locators: set[tuple[UUID, int]] = set()
        staging_rows: list[_StagingRow] = []

        for occurrence in files:
            if occurrence.status is not ImportJobFileStatus.READY:
                continue
            record = records_by_id.get(occurrence.id)
            if record is None or record.stored_file.id != occurrence.stored_file_id:
                raise ImportDomainError(
                    "IMPORT_PREVIEW_REVALIDATION_FAILED",
                    "Preview file lineage changed during revalidation",
                )
            mapping = dict(occurrence.field_mapping or {})
            if not mapping or occurrence.mapping_hash != hash_document(mapping):
                raise ImportDomainError(
                    "IMPORT_PREVIEW_REVALIDATION_FAILED",
                    "Preview file mapping changed during revalidation",
                )

            stored_file = record.stored_file
            content = await self.storage.read(
                stored_file.storage_key,
                expected_size=stored_file.size,
                expected_sha256=stored_file.sha256,
            )
            table = parse_table(
                content,
                file_type=stored_file.detected_type,
                declared_mime=occurrence.declared_mime or stored_file.detected_mime,
                limits=self.parser_limits,
                repair_huitun_dimensions=(job.source_type is ImportSourceType.MANUAL_HUITUN_EXPORT),
            )
            snapshot = _TaskSnapshot(
                job_id=job.id,
                file_id=occurrence.id,
                stored_file_id=stored_file.id,
                storage_key=stored_file.storage_key,
                expected_size=stored_file.size,
                expected_sha256=stored_file.sha256,
                file_type=stored_file.detected_type,
                declared_mime=occurrence.declared_mime or stored_file.detected_mime,
                source_type=job.source_type,
                field_mapping=mapping,
                task_id=job.parse_task_id or "",
            )
            validated_mapping, adapter = self._mapping_and_adapter(snapshot, table)
            if (
                adapter is None
                or validated_mapping != mapping
                or hash_document(validated_mapping) != occurrence.mapping_hash
            ):
                raise ImportDomainError(
                    "IMPORT_PREVIEW_REVALIDATION_FAILED",
                    "Preview file mapping no longer matches its source fields",
                )

            adapter.validate_table(table.rows)
            adapted_rows = [adapter.adapt(raw_row) for raw_row in table.rows]
            for index, adapted in enumerate(adapted_rows):
                locator = (occurrence.id, adapted.row_number)
                model = models_by_locator.get(locator)
                import_row_id = (
                    model.id
                    if model is not None
                    else uuid5(job.id, f"import-row:{occurrence.id}:{adapted.row_number}")
                )
                warnings = list(adapted.warning_dicts())
                if index == 0:
                    warnings.extend(dict(warning) for warning in table.warnings)
                staging_rows.append(
                    _StagingRow(
                        occurrence=occurrence,
                        import_row_id=import_row_id,
                        row_number=adapted.row_number,
                        raw_data=dict(adapted.raw_data),
                        normalized_data=adapted.normalized_data(),
                        record=adapted.record,
                        mapping_hash=occurrence.mapping_hash,
                        initial_warnings=warnings,
                        initial_errors=list(adapted.error_dicts()),
                        model=model,
                    )
                )
                rebuilt_locators.add(locator)

        if not staging_rows or len(staging_rows) > self.max_batch_rows:
            raise ImportDomainError(
                "IMPORT_BATCH_ROW_LIMIT",
                "Included import batch has an invalid row count",
                status_code=409,
                details={"max_batch_rows": self.max_batch_rows},
            )

        # Staging is only a locator cache.  Revalidation is authoritative: an
        # unexpected persisted row is removed in the same transaction that will
        # publish the rebuilt Preview, never as a separate Task 3 mutation.
        if mode is RevalidationMode.PREVIEW_REPLACE:
            for model in persisted_models:
                if (model.import_job_file_id, model.row_number) not in rebuilt_locators:
                    await self.session.delete(model)
        staging_rows.sort(key=lambda item: item.locator.sort_key)
        return staging_rows

    async def _task_snapshot(
        self,
        import_job_id: UUID,
        import_job_file_id: UUID,
        task_id: str,
    ) -> tuple[_TaskSnapshot | None, dict[str, Any] | None]:
        job = await self.repository.get_import_job(import_job_id)
        if job is None:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found", status_code=404)
        self._require_bulk_draft(job)
        record = await self.repository.get_import_job_file_record(import_job_id, import_job_file_id)
        if record is None:
            raise ImportDomainError(
                "IMPORT_JOB_FILE_NOT_FOUND", "Import job file not found", status_code=404
            )
        occurrence = record.occurrence
        if occurrence.status is ImportJobFileStatus.EXCLUDED:
            result = self._file_result(job, occurrence)
            await self.session.rollback()
            return None, result
        if occurrence.status is not ImportJobFileStatus.PARSING:
            result = self._file_result(job, occurrence)
            await self.session.rollback()
            return None, result
        if occurrence.parse_task_id != task_id:
            result = self._file_result(job, occurrence)
            await self.session.rollback()
            return None, result

        snapshot = _TaskSnapshot(
            job_id=job.id,
            file_id=occurrence.id,
            stored_file_id=record.stored_file.id,
            storage_key=record.stored_file.storage_key,
            expected_size=record.stored_file.size,
            expected_sha256=record.stored_file.sha256,
            file_type=record.stored_file.detected_type,
            declared_mime=occurrence.declared_mime or record.stored_file.detected_mime,
            source_type=job.source_type,
            field_mapping=(
                dict(occurrence.field_mapping) if occurrence.field_mapping is not None else None
            ),
            task_id=task_id,
        )
        # Parsing and storage verification do not need to retain a database
        # snapshot.  The final transaction locks the Job and rechecks every
        # persisted token/fingerprint before replacing staging rows.
        await self.session.rollback()
        return snapshot, None

    def _mapping_and_adapter(
        self,
        snapshot: _TaskSnapshot,
        table: ParsedTable,
    ) -> tuple[dict[str, str], SourceAdapter | None]:
        mapping = dict(snapshot.field_mapping or {})
        if mapping:
            adapter = self._adapter(snapshot.source_type, table.file_type, mapping)
            return adapter.mapping_for_headers(table.headers), adapter

        if snapshot.source_type is ImportSourceType.MANUAL_HUITUN_EXPORT:
            candidate = self._adapter(snapshot.source_type, table.file_type, None)
            try:
                return candidate.mapping_for_headers(table.headers), candidate
            except ImportDomainError as exc:
                if exc.code != "MAPPING_INVALID":
                    raise
                partial_mapping = {
                    source: target
                    for source, target in HUITUN_FIELD_MAPPING.items()
                    if source in table.headers
                }
                return partial_mapping, None
        return {}, None

    @staticmethod
    def _adapter(
        source_type: ImportSourceType,
        file_type: StoredFileType,
        mapping: dict[str, str] | None,
    ) -> SourceAdapter:
        if source_type is ImportSourceType.MANUAL_HUITUN_EXPORT:
            adapter_type = (
                HuitunCsvAdapter if file_type is StoredFileType.CSV else HuitunExcelAdapter
            )
            return adapter_type(mapping)
        if source_type is ImportSourceType.GENERIC_CSV:
            if file_type is not StoredFileType.CSV:
                raise ImportDomainError(
                    "UNSUPPORTED_SOURCE_FILE_COMBINATION",
                    "Generic source currently supports CSV only",
                )
            if not mapping:
                raise ImportDomainError("MAPPING_REQUIRED", "Generic CSV requires mapping")
            return GenericCsvAdapter(mapping)
        raise ImportDomainError("UNSUPPORTED_SOURCE", "Unsupported import source")

    async def _persist_mapping_required(
        self,
        snapshot: _TaskSnapshot,
        table: ParsedTable,
        mapping: dict[str, str],
        *,
        task_context: tuple[UUID, int] | None = None,
    ) -> dict[str, Any]:
        job, occurrence, stored_file = await self._locked_current_task(snapshot)
        if stored_file is None:
            result = self._file_result(job, occurrence)
            await self.session.rollback()
            return result

        await self.repository.delete_import_rows_for_file(occurrence.id)
        now = datetime.now(UTC)
        occurrence.status = ImportJobFileStatus.MAPPING_REQUIRED
        occurrence.detected_fields = list(table.headers)
        occurrence.field_mapping = mapping or None
        occurrence.mapping_hash = hash_document(mapping) if mapping else None
        occurrence.raw_rows = len(table.rows)
        occurrence.warning_rows = sum(bool(row.warnings) for row in table.rows) + int(
            bool(table.warnings)
        )
        occurrence.error_rows = sum(bool(row.errors) for row in table.rows)
        occurrence.error_code = None
        occurrence.error_message = None
        occurrence.parse_completed_at = now
        stored_file.encoding = table.encoding
        stored_file.parse_metadata = {
            "delimiter": table.delimiter,
            "header_count": len(table.headers),
        }
        files = await self.repository.list_import_job_files(job.id, for_update=True)
        await self._persist_replanned_rows(job, files, replacement=None)
        await self._complete_task(task_context)
        await self.session.commit()
        return self._file_result(job, occurrence)

    async def _persist_parsed_file(
        self,
        snapshot: _TaskSnapshot,
        table: ParsedTable,
        mapping: dict[str, str],
        adapted_rows: list[AdaptedRow],
        *,
        task_context: tuple[UUID, int] | None = None,
    ) -> dict[str, Any]:
        job, occurrence, stored_file = await self._locked_current_task(snapshot)
        if stored_file is None:
            result = self._file_result(job, occurrence)
            await self.session.rollback()
            return result

        files = await self.repository.list_import_job_files(job.id, for_update=True)
        included_row_count = sum(
            item.raw_rows
            for item in files
            if item.id != occurrence.id and item.status is ImportJobFileStatus.READY
        ) + len(adapted_rows)
        if included_row_count > self.max_batch_rows:
            raise ImportDomainError(
                "IMPORT_BATCH_ROW_LIMIT",
                "Included import batch exceeds the maximum row count",
                details={"max_batch_rows": self.max_batch_rows},
            )

        existing_rows = await self._all_job_rows(job.id)
        existing_target = {
            row.row_number: row for row in existing_rows if row.import_job_file_id == occurrence.id
        }
        mapping_hash = hash_document(mapping)
        replacement: list[_StagingRow] = []
        for index, adapted in enumerate(adapted_rows):
            model = existing_target.get(adapted.row_number)
            import_row_id = (
                model.id
                if model is not None
                else uuid5(job.id, f"import-row:{occurrence.id}:{adapted.row_number}")
            )
            warnings: list[dict[str, Any]] = list(adapted.warning_dicts())
            if index == 0:
                warnings.extend(dict(warning) for warning in table.warnings)
            replacement.append(
                _StagingRow(
                    occurrence=occurrence,
                    import_row_id=import_row_id,
                    row_number=adapted.row_number,
                    raw_data=dict(adapted.raw_data),
                    normalized_data=adapted.normalized_data(),
                    record=adapted.record,
                    mapping_hash=mapping_hash,
                    initial_warnings=warnings,
                    initial_errors=list(adapted.error_dicts()),
                    model=model,
                )
            )

        retained_numbers = {row.row_number for row in replacement}
        for row_number, stale in existing_target.items():
            if row_number not in retained_numbers:
                await self.session.delete(stale)

        now = datetime.now(UTC)
        occurrence.status = ImportJobFileStatus.READY
        occurrence.detected_fields = list(table.headers)
        occurrence.field_mapping = dict(mapping)
        occurrence.mapping_hash = mapping_hash
        occurrence.raw_rows = len(adapted_rows)
        occurrence.error_code = None
        occurrence.error_message = None
        occurrence.parse_completed_at = now
        occurrence.excluded_at = None
        stored_file.encoding = table.encoding
        stored_file.parse_metadata = {
            "delimiter": table.delimiter,
            "header_count": len(table.headers),
        }
        # READY becomes visible together with the complete batch replan.  Setting
        # it before planning also lets per-file warning/error counts include the
        # replacement rows; the surrounding transaction rolls it back if any
        # planning or persistence step fails.
        await self._persist_replanned_rows(job, files, replacement=replacement)
        await self._complete_task(task_context)
        await self.session.commit()
        return self._file_result(job, occurrence)

    async def _locked_current_task(
        self,
        snapshot: _TaskSnapshot,
    ) -> tuple[ImportJob, ImportJobFile, StoredImportFile | None]:
        # PostgreSQL workers serialize the expensive final graph replacement on
        # one transaction-scoped Job key.  The persisted task token remains the
        # authority after the lock is acquired; the advisory lock is only the
        # concurrency primitive and cannot resurrect a stale delivery.
        await self.repository.acquire_identity_locks([f"phase2:batch-import-job:{snapshot.job_id}"])
        job = await self.repository.get_import_job(snapshot.job_id, for_update=True)
        self._require_bulk_draft(job)
        assert job is not None
        occurrence = await self.repository.get_import_job_file(snapshot.file_id, for_update=True)
        if occurrence is None or occurrence.import_job_id != snapshot.job_id:
            raise ImportDomainError(
                "IMPORT_JOB_FILE_NOT_FOUND", "Import job file not found", status_code=404
            )
        if (
            occurrence.status is not ImportJobFileStatus.PARSING
            or occurrence.parse_task_id != snapshot.task_id
            or occurrence.stored_file_id != snapshot.stored_file_id
        ):
            return job, occurrence, None
        stored_file = await self.repository.get_stored_file(occurrence.stored_file_id)
        if stored_file is None:
            raise ImportDomainError("FILE_NOT_FOUND", "Stored import file not found")
        if (
            stored_file.storage_key != snapshot.storage_key
            or stored_file.size != snapshot.expected_size
            or stored_file.sha256 != snapshot.expected_sha256
        ):
            return job, occurrence, None
        return job, occurrence, stored_file

    async def _persist_replanned_rows(
        self,
        job: ImportJob,
        files: Sequence[ImportJobFile],
        *,
        replacement: list[_StagingRow] | None,
    ) -> int:
        all_models = await self._all_job_rows(job.id)
        models_by_locator = {(row.import_job_file_id, row.row_number): row for row in all_models}
        replacement_file_id = replacement[0].occurrence.id if replacement else None
        staging_rows: list[_StagingRow] = []
        files_by_id = {item.id: item for item in files}
        for persisted_model in all_models:
            occurrence = files_by_id.get(persisted_model.import_job_file_id)
            if occurrence is None or occurrence.status is not ImportJobFileStatus.READY:
                continue
            if replacement_file_id is not None and occurrence.id == replacement_file_id:
                continue
            if persisted_model.normalized_data is None:
                raise ImportDomainError(
                    "IMPORT_STAGING_INVALID", "Ready import row lacks normalized data"
                )
            record = CanonicalInfluencerRecord.model_validate(persisted_model.normalized_data)
            retained_table_warnings = [
                dict(warning)
                for warning in persisted_model.warnings
                if warning.get("code") == "WARNINGS_TRUNCATED"
            ]
            staging_rows.append(
                _StagingRow(
                    occurrence=occurrence,
                    import_row_id=persisted_model.id,
                    row_number=persisted_model.row_number,
                    raw_data=dict(persisted_model.raw_data),
                    normalized_data=dict(persisted_model.normalized_data),
                    record=record,
                    mapping_hash=(
                        occurrence.mapping_hash
                        or hash_document(dict(occurrence.field_mapping or {}))
                    ),
                    initial_warnings=[
                        *(issue.as_dict() for issue in record.warnings),
                        *retained_table_warnings,
                    ],
                    initial_errors=[issue.as_dict() for issue in record.errors],
                    model=persisted_model,
                )
            )
        if replacement:
            staging_rows.extend(replacement)
        staging_rows.sort(key=lambda item: item.locator.sort_key)
        if len(staging_rows) > self.max_batch_rows:
            raise ImportDomainError(
                "IMPORT_BATCH_ROW_LIMIT",
                "Included import batch exceeds the maximum row count",
                details={"max_batch_rows": self.max_batch_rows},
            )

        plans = await self._plan_staging(job, staging_rows)
        for staged, plan in zip(staging_rows, plans, strict=True):
            current_model = staged.model or models_by_locator.get(
                (staged.occurrence.id, staged.row_number)
            )
            if current_model is None:
                current_model = ImportRow(
                    id=staged.import_row_id,
                    import_job_id=job.id,
                    import_job_file_id=staged.occurrence.id,
                    row_number=staged.row_number,
                )
                self.session.add(current_model)
                staged.model = current_model
            current_model.raw_data = staged.raw_data
            current_model.normalized_data = staged.normalized_data
            current_model.matched_influencer_id = plan.matched_influencer_id
            current_model.matched_platform_account_id = plan.matched_platform_account_id
            current_model.match_type = plan.match_type
            current_model.action = plan.action
            current_model.merge_plan = plan.merge_plan
            current_model.warnings = plan.warnings
            current_model.errors = plan.errors
            current_model.preview_revision = 0
            current_model.plan_hash = plan.plan_hash
            current_model.committed_action = None
            current_model.committed_at = None

        planned_by_file: dict[UUID, list[PlannedImportRow]] = defaultdict(list)
        for staged, plan in zip(staging_rows, plans, strict=True):
            planned_by_file[staged.occurrence.id].append(plan)
        for occurrence in files:
            if occurrence.status is not ImportJobFileStatus.READY:
                continue
            file_plans = planned_by_file.get(occurrence.id, [])
            occurrence.raw_rows = len(file_plans)
            occurrence.warning_rows = sum(bool(plan.warnings) for plan in file_plans)
            occurrence.error_rows = sum(bool(plan.errors) for plan in file_plans)
        return len(staging_rows)

    async def _plan_staging(
        self,
        job: ImportJob,
        staging_rows: Sequence[_StagingRow],
        *,
        preview_revision: int = 0,
    ) -> list[PlannedImportRow]:
        plans, _ = await self.plan_preview_staging(
            job,
            staging_rows,
            preview_revision=preview_revision,
        )
        return plans

    async def plan_preview_staging(
        self,
        job: ImportJob,
        staging_rows: Sequence[_StagingRow],
        *,
        preview_revision: int,
    ) -> tuple[list[PlannedImportRow], PrefetchedImportState]:
        """Plan a complete ordered batch and expose its frozen prefetch state."""

        valid_batch_rows = [
            BatchRow(
                locator=row.locator,
                record=row.record,
                normalized_data=row.normalized_data,
            )
            for row in staging_rows
            if not row.initial_errors
        ]
        graph = build_identity_graph(valid_batch_rows)
        records = [row.record for row in valid_batch_rows]
        bulk_context = BulkImportContext.from_records(
            records,
            snapshot_keys=(metric_snapshot_key(record) for record in records),
        )
        prefetched_state = await BulkImportRepository(self.session).prefetch(bulk_context)
        planner = ImportPlanner(PrefetchedImportRepository(prefetched_state))
        database_matches, match_results, hard_conflicts = await self._database_matches(
            graph.components,
            planner,
            prefetched_state,
        )
        resolutions = list(resolve_identity_graph(graph, database_matches=database_matches))
        for index, resolution in enumerate(resolutions):
            if resolution.component.group_id in hard_conflicts:
                resolutions[index] = self._database_conflict_resolution(
                    resolution.component, database_matches
                )
        resolution_by_locator = {
            row.locator.identity: resolution
            for resolution in resolutions
            for row in resolution.component.rows
        }
        duplicate_annotation_by_locator = {
            annotation.duplicate.identity: annotation
            for resolution in resolutions
            for annotation in resolution.duplicate_annotations()
        }
        duplicate_emails = duplicate_email_values(valid_batch_rows, graph.components)

        plans: list[PlannedImportRow] = []
        for staged in staging_rows:
            locator_document = staged.locator.as_dict(include_import_row_id=True)
            if staged.initial_errors:
                plans.append(
                    await planner.plan(
                        job_id=job.id,
                        row_number=staged.row_number,
                        record=staged.record,
                        normalized_data=staged.normalized_data,
                        mapping_hash=staged.mapping_hash,
                        preview_revision=preview_revision,
                        initial_warnings=list(staged.initial_warnings),
                        initial_errors=list(staged.initial_errors),
                        possible_duplicate_emails=duplicate_emails,
                        row_locator=locator_document,
                    )
                )
                continue

            resolution = resolution_by_locator[staged.locator.identity]
            if resolution.kind is ComponentResolutionKind.MANUAL_REVIEW:
                warning = resolution.manual_review_warning()
                merge_plan = resolution.manual_review_merge_plan()
                assert warning is not None and merge_plan is not None
                plans.append(
                    planner.plan_batch_manual_review(
                        job_id=job.id,
                        row_number=staged.row_number,
                        normalized_data=staged.normalized_data,
                        mapping_hash=staged.mapping_hash,
                        row_locator=locator_document,
                        merge_plan=merge_plan,
                        warnings=[*staged.initial_warnings, warning],
                        errors=list(staged.initial_errors),
                        preview_revision=preview_revision,
                    )
                )
                continue

            assert resolution.owner is not None
            if staged.locator != resolution.owner.locator:
                annotation = duplicate_annotation_by_locator[staged.locator.identity]
                plans.append(
                    planner.plan_batch_duplicate(
                        job_id=job.id,
                        row_number=staged.row_number,
                        normalized_data=staged.normalized_data,
                        mapping_hash=staged.mapping_hash,
                        row_locator=locator_document,
                        merge_plan=annotation.merge_plan(),
                        warnings=[*staged.initial_warnings, annotation.warning()],
                        errors=list(staged.initial_errors),
                        preview_revision=preview_revision,
                    )
                )
                continue

            match_override = await self._component_match_override(
                resolution.component,
                database_matches,
                match_results,
                staged.locator,
                prefetched_state,
            )
            plans.append(
                await planner.plan(
                    job_id=job.id,
                    row_number=staged.row_number,
                    record=staged.record,
                    normalized_data=staged.normalized_data,
                    mapping_hash=staged.mapping_hash,
                    preview_revision=preview_revision,
                    initial_warnings=list(staged.initial_warnings),
                    initial_errors=list(staged.initial_errors),
                    possible_duplicate_emails=duplicate_emails,
                    row_locator=locator_document,
                    match_override=match_override,
                )
            )
        return plans, prefetched_state

    async def _database_matches(
        self,
        components: Sequence[IdentityComponent],
        planner: ImportPlanner,
        prefetched_state: PrefetchedImportState,
    ) -> tuple[
        dict[str, tuple[DatabaseIdentityTarget, ...]],
        dict[tuple[UUID, int], MatchResult],
        set[str],
    ]:
        targets_by_key: dict[str, set[DatabaseIdentityTarget]] = defaultdict(set)
        results: dict[tuple[UUID, int], MatchResult] = {}
        cached_results: dict[str, MatchResult] = {}
        hard_conflicts: set[str] = set()
        for component in components:
            for row in component.rows:
                cache_key = hash_document(row.record.as_dict())
                match = cached_results.get(cache_key)
                if match is None:
                    match = await planner.match(row.record)
                    cached_results[cache_key] = match
                results[row.locator.identity] = match
                if match.manual_review and any(
                    warning.code == "IDENTITY_CONFLICT" for warning in match.warnings
                ):
                    hard_conflicts.add(component.group_id)
                for basis, account_ids in match.candidates.items():
                    hard_key = self._hard_key_for_basis(row.record, basis)
                    if hard_key is None:
                        continue
                    for account_id in account_ids:
                        account = prefetched_state.accounts_by_id.get(UUID(account_id))
                        if account is None:
                            raise ImportDomainError(
                                "IMPORT_STAGING_INVALID",
                                "Matched platform account is absent from the prefetch context",
                            )
                        targets_by_key[hard_key].add(
                            DatabaseIdentityTarget(
                                platform_account_id=account.id,
                                influencer_id=account.influencer_id,
                            )
                        )
        return (
            {
                key: tuple(
                    sorted(
                        values,
                        key=lambda item: (
                            str(item.platform_account_id),
                            str(item.influencer_id),
                        ),
                    )
                )
                for key, values in targets_by_key.items()
            },
            results,
            hard_conflicts,
        )

    @staticmethod
    def _hard_key_for_basis(
        record: CanonicalInfluencerRecord,
        basis: str,
    ) -> str | None:
        identity = record.platform_identity
        platform = identity.platform.value
        if basis == ImportMatchType.PLATFORM_ACCOUNT_ID.value and identity.platform_account_id:
            return f"platform:{platform}:account:{identity.platform_account_id}"
        if basis == ImportMatchType.EXTERNAL_SOURCE_ID.value and identity.external_source_id:
            return (
                f"source:{record.source.value}:platform:{platform}:external:"
                f"{identity.external_source_id}"
            )
        if (
            basis == ImportMatchType.NORMALIZED_PROFILE_URL.value
            and identity.normalized_profile_url
        ):
            return f"platform:{platform}:profile:{identity.normalized_profile_url}"
        return None

    @staticmethod
    def _database_conflict_resolution(
        component: IdentityComponent,
        database_matches: DatabaseIdentityMatches,
    ) -> ComponentResolution:
        evidence = tuple(
            sorted(
                {
                    DatabaseIdentityEvidence(
                        hard_identity=key,
                        platform_account_id=target.platform_account_id,
                        influencer_id=target.influencer_id,
                    )
                    for key in component.hard_identity_keys
                    for target in database_matches.get(key, ())
                },
                key=lambda item: (
                    item.hard_identity,
                    str(item.platform_account_id),
                    str(item.influencer_id),
                ),
            )
        )
        return ComponentResolution(
            component=component,
            kind=ComponentResolutionKind.MANUAL_REVIEW,
            owner=None,
            duplicates=(),
            manual_review_reason=ManualReviewReason.DATABASE_IDENTITY_CONFLICT,
            database_identity_evidence=evidence,
        )

    async def _component_match_override(
        self,
        component: IdentityComponent,
        database_matches: DatabaseIdentityMatches,
        match_results: Mapping[tuple[UUID, int], MatchResult],
        owner_locator: RowLocator,
        prefetched_state: PrefetchedImportState,
    ) -> MatchResult:
        targets = {
            target
            for key in component.hard_identity_keys
            for target in database_matches.get(key, ())
        }
        if not targets:
            return match_results[owner_locator.identity]
        target = next(iter(targets))
        account = prefetched_state.accounts_by_id.get(target.platform_account_id)
        if account is None:
            raise ImportDomainError(
                "IMPORT_STAGING_INVALID", "Matched platform account no longer exists"
            )
        candidates: dict[str, list[str]] = {}
        priority = (
            (":account:", ImportMatchType.PLATFORM_ACCOUNT_ID),
            (":external:", ImportMatchType.EXTERNAL_SOURCE_ID),
            (":profile:", ImportMatchType.NORMALIZED_PROFILE_URL),
        )
        matched_type = ImportMatchType.NONE
        for marker, match_type in priority:
            account_ids = {
                str(item.platform_account_id)
                for key in component.hard_identity_keys
                if marker in key
                for item in database_matches.get(key, ())
            }
            if account_ids:
                candidates[match_type.value] = sorted(account_ids)
                if matched_type is ImportMatchType.NONE:
                    matched_type = match_type
        return MatchResult(
            account=account,
            match_type=matched_type,
            manual_review=False,
            warnings=(),
            candidates=candidates,
        )

    async def _all_job_rows(self, import_job_id: UUID) -> list[ImportRow]:
        return list(
            await self.session.scalars(
                select(ImportRow)
                .where(ImportRow.import_job_id == import_job_id)
                .order_by(ImportRow.import_job_file_id, ImportRow.row_number, ImportRow.id)
            )
        )

    async def _mark_failed(
        self,
        import_job_id: UUID,
        import_job_file_id: UUID,
        task_id: str,
        exc: BaseException,
    ) -> None:
        try:
            job = await self.repository.get_import_job(import_job_id, for_update=True)
            if (
                job is None
                or job.status is not ImportJobStatus.DRAFT
                or job.preview_revision != 0
                or job.stored_file_id is not None
            ):
                await self.session.rollback()
                return
            occurrence = await self.repository.get_import_job_file(
                import_job_file_id, for_update=True
            )
            if (
                occurrence is None
                or occurrence.import_job_id != job.id
                or occurrence.status is not ImportJobFileStatus.PARSING
                or occurrence.parse_task_id != task_id
            ):
                await self.session.rollback()
                return
            now = datetime.now(UTC)
            occurrence.status = ImportJobFileStatus.FAILED
            occurrence.parse_completed_at = now
            occurrence.error_code = (
                exc.code if isinstance(exc, ImportDomainError) else "IMPORT_PROCESSING_FAILED"
            )
            occurrence.error_message = (
                exc.message
                if isinstance(exc, ImportDomainError)
                else "Import file processing failed safely"
            )
            files = await self.repository.list_import_job_files(job.id, for_update=True)
            await self._persist_replanned_rows(job, files, replacement=None)
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            # Failure state is more important than a secondary replan.  Retry a
            # minimal guarded marker when planning itself encountered damage.
            job = await self.repository.get_import_job(import_job_id, for_update=True)
            occurrence = await self.repository.get_import_job_file(
                import_job_file_id, for_update=True
            )
            if (
                job is not None
                and job.status is ImportJobStatus.DRAFT
                and job.preview_revision == 0
                and job.stored_file_id is None
                and occurrence is not None
                and occurrence.import_job_id == job.id
                and occurrence.status is ImportJobFileStatus.PARSING
                and occurrence.parse_task_id == task_id
            ):
                occurrence.status = ImportJobFileStatus.FAILED
                occurrence.parse_completed_at = datetime.now(UTC)
                occurrence.error_code = (
                    exc.code if isinstance(exc, ImportDomainError) else "IMPORT_PROCESSING_FAILED"
                )
                occurrence.error_message = (
                    exc.message
                    if isinstance(exc, ImportDomainError)
                    else "Import file processing failed safely"
                )
                await self.session.commit()
            else:
                await self.session.rollback()

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
                "File Parse lost its authoritative task lease before commit",
            )

    @staticmethod
    def _require_bulk_draft(job: ImportJob | None) -> None:
        if job is None:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found", status_code=404)
        if (
            job.status is not ImportJobStatus.DRAFT
            or job.preview_revision != 0
            or job.stored_file_id is not None
        ):
            raise ImportDomainError(
                "INVALID_IMPORT_MODE",
                "File parsing requires a draft bulk import job",
                status_code=409,
            )

    @staticmethod
    def _file_result(
        job: ImportJob,
        occurrence: ImportJobFile | None,
    ) -> dict[str, Any]:
        return {
            "import_job_id": str(job.id),
            "import_job_file_id": str(occurrence.id) if occurrence is not None else None,
            "status": (
                occurrence.status.value
                if occurrence is not None
                else ImportJobFileStatus.PARSING.value
            ),
            "raw_rows": occurrence.raw_rows if occurrence is not None else 0,
        }


__all__ = ["BatchImportProcessor"]
