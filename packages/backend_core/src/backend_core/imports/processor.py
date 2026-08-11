"""Atomic worker-side parsing, preview planning, validation, and commit."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
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
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    CRMStage,
    DataSource,
)
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)


class PreviewStaleError(Exception):
    pass


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class ImportProcessor:
    """The only implementation of worker-side Phase 1B business rules."""

    def __init__(
        self,
        session: AsyncSession,
        storage: StorageAdapter,
        *,
        parser_limits: ParserLimits,
    ) -> None:
        self.session = session
        self.storage = storage
        self.repository = ImportRepository(session)
        self.audit = AuditRepository(session)
        self.parser_limits = parser_limits

    async def parse_and_preview(self, import_job_id: UUID) -> dict[str, Any]:
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
                await self._mark_mapping_required(job.id, occurrence.id, table, mapping)
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
            )
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            await self.session.rollback()
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
        await self.session.commit()

    async def _persist_preview(
        self,
        job_id: UUID,
        import_job_file_id: UUID,
        table: ParsedTable,
        mapping: dict[str, str],
        adapted_rows: list[AdaptedRow],
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

    async def confirm(self, import_job_id: UUID, preview_revision: int) -> dict[str, Any]:
        try:
            early_result = await self._begin_importing(import_job_id, preview_revision)
            if early_result is not None:
                return early_result
            return await self._validate_and_commit(import_job_id, preview_revision)
        except PreviewStaleError as exc:
            await self.session.rollback()
            await self._mark_stale(import_job_id, preview_revision, str(exc))
            return {
                "import_job_id": str(import_job_id),
                "status": ImportJobStatus.PREVIEW_STALE.value,
                "error_code": "PREVIEW_STALE",
            }
        except IntegrityError:
            await self.session.rollback()
            await self._mark_stale(import_job_id, preview_revision, "database_identity_changed")
            return {
                "import_job_id": str(import_job_id),
                "status": ImportJobStatus.PREVIEW_STALE.value,
                "error_code": "PREVIEW_STALE",
            }
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            await self.session.rollback()
            await self._mark_failed(import_job_id, exc, mark_file_failed=False)
            if isinstance(exc, ImportDomainError):
                raise
            raise ImportDomainError(
                "IMPORT_PROCESSING_FAILED", "Import processing failed safely"
            ) from None

    async def _begin_importing(
        self, import_job_id: UUID, preview_revision: int
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
        self, import_job_id: UUID, preview_revision: int
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
        if plan.action in {
            ImportRowAction.ERROR,
            ImportRowAction.SKIP,
            ImportRowAction.MANUAL_REVIEW,
        }:
            row.committed_action = plan.action
            row.committed_at = now
            return
        merge = plan.merge_plan
        account: InfluencerPlatformAccount
        if plan.action == ImportRowAction.CREATE:
            create = merge["account_create"]
            influencer = Influencer(
                display_name=create["display_name"],
                owner_operator_id=job.operator_id,
                crm_stage=CRMStage.TO_DEVELOP,
            )
            self.session.add(influencer)
            await self.session.flush()
            public_profile = create["public_profile"]
            account = InfluencerPlatformAccount(
                influencer_id=influencer.id,
                platform=record.platform_identity.platform,
                platform_account_id=create["platform_account_id"],
                account_name=create["account_name"],
                account_handle=create["account_handle"],
                profile_url=create["profile_url"],
                normalized_profile_url=create["normalized_profile_url"],
                source=record.source,
                is_active=True,
                bio=public_profile.get("bio"),
                gender=public_profile.get("gender"),
                region_raw=public_profile.get("region_raw"),
                verification_info=public_profile.get("verification_info"),
                mcn_name=public_profile.get("mcn_name"),
                source_tags=public_profile.get("creator_tags"),
                creator_level=public_profile.get("creator_level"),
                is_brand_partner=public_profile.get("is_brand_partner"),
            )
            self.session.add(account)
            await self.session.flush()
        else:
            if plan.matched_platform_account_id is None:
                raise PreviewStaleError("matched_account_missing")
            matched_account = await self.session.get(
                InfluencerPlatformAccount, plan.matched_platform_account_id
            )
            if matched_account is None:
                raise PreviewStaleError("matched_account_deleted")
            account = matched_account
            for field, value in merge["account_updates"].items():
                setattr(account, field, value)

        row.matched_influencer_id = account.influencer_id
        row.matched_platform_account_id = account.id
        source_identity_plan = merge.get("source_identity")
        if source_identity_plan:
            self.session.add(
                PlatformAccountSourceIdentity(
                    platform_account_id=account.id,
                    platform=account.platform,
                    source=record.source,
                    external_account_id=source_identity_plan["external_account_id"],
                    first_import_job_id=job.id,
                    first_import_row_id=row.id,
                    last_import_job_id=job.id,
                    last_import_row_id=row.id,
                )
            )

        source_state_plan = merge.get("source_state")
        if source_state_plan:
            source_state = await self.repository.get_source_state(account.id, record.source)
            if source_state is None:
                source_state = InfluencerSourceState(
                    influencer_id=account.influencer_id,
                    platform_account_id=account.id,
                    source=record.source,
                    source_updated_at=_parse_datetime(source_state_plan["source_updated_at"]),
                    source_data=source_state_plan["source_data"],
                    source_data_hash=source_state_plan["source_data_hash"],
                    state_version=source_state_plan["state_version"],
                    last_import_job_id=job.id,
                    last_import_row_id=row.id,
                )
                self.session.add(source_state)
            else:
                source_state.source_updated_at = _parse_datetime(
                    source_state_plan["source_updated_at"]
                )
                source_state.source_data = source_state_plan["source_data"]
                source_state.source_data_hash = source_state_plan["source_data_hash"]
                source_state.state_version = source_state_plan["state_version"]
                source_state.last_import_job_id = job.id
                source_state.last_import_row_id = row.id

        await self._apply_contacts(job, row, record, account, merge["contacts"], now)
        await self._apply_metrics(job, row, record, account, merge["metrics"], now)
        row.committed_action = plan.action
        row.committed_at = now

    async def _apply_contacts(
        self,
        job: ImportJob,
        row: ImportRow,
        record: CanonicalInfluencerRecord,
        account: InfluencerPlatformAccount,
        contacts_plan: dict[str, Any],
        now: datetime,
    ) -> None:
        for contact_id in contacts_plan["deactivate_ids"]:
            contact = await self.session.get(InfluencerContact, UUID(contact_id))
            if contact is not None and contact.source != DataSource.MANUAL:
                contact.is_current = False
        for contact_id in contacts_plan["mark_duplicate_ids"]:
            contact = await self.session.get(InfluencerContact, UUID(contact_id))
            if contact is not None and contact.source != DataSource.MANUAL:
                contact.possible_duplicate_contact = True
        for contact_id in contacts_plan["observe_ids"]:
            contact = await self.session.get(InfluencerContact, UUID(contact_id))
            if contact is not None and contact.source != DataSource.MANUAL:
                contact.last_seen_at = now
                contact.last_import_job_id = job.id
                contact.last_import_row_id = row.id
        for item in contacts_plan["create"]:
            self.session.add(
                InfluencerContact(
                    influencer_id=account.influencer_id,
                    platform_account_id=account.id,
                    type=ContactType(item["type"]),
                    value=item["value"],
                    normalized_value=item["normalized_value"],
                    source=record.source,
                    validation_status=ContactValidationStatus(item["validation_status"]),
                    is_current=item["is_current"],
                    possible_duplicate_contact=item["possible_duplicate_contact"],
                    first_seen_at=now,
                    last_seen_at=now,
                    source_updated_at=_parse_datetime(item["source_updated_at"]),
                    first_import_job_id=job.id,
                    first_import_row_id=row.id,
                    last_import_job_id=job.id,
                    last_import_row_id=row.id,
                )
            )

    async def _apply_metrics(
        self,
        job: ImportJob,
        row: ImportRow,
        record: CanonicalInfluencerRecord,
        account: InfluencerPlatformAccount,
        metrics_plan: dict[str, Any],
        now: datetime,
    ) -> None:
        current_plan = metrics_plan.get("current")
        if current_plan:
            current = await self.repository.get_current_metrics(account.id, record.source)
            if current is None:
                current = InfluencerCurrentMetrics(
                    influencer_id=account.influencer_id,
                    platform_account_id=account.id,
                    source=record.source,
                    source_updated_at=_parse_datetime(current_plan["source_updated_at"]),
                    metrics=current_plan["metrics"],
                    metrics_hash=current_plan["metrics_hash"],
                    last_import_job_id=job.id,
                    last_import_row_id=row.id,
                )
                self.session.add(current)
            else:
                current.source_updated_at = _parse_datetime(current_plan["source_updated_at"])
                current.metrics = current_plan["metrics"]
                current.metrics_hash = current_plan["metrics_hash"]
                current.last_import_job_id = job.id
                current.last_import_row_id = row.id
        snapshot_plan = metrics_plan.get("snapshot")
        if snapshot_plan:
            self.session.add(
                InfluencerMetricSnapshot(
                    influencer_id=account.influencer_id,
                    platform_account_id=account.id,
                    source=record.source,
                    source_updated_at=_parse_datetime(snapshot_plan["source_updated_at"]),
                    import_job_id=job.id,
                    import_row_id=row.id,
                    captured_at=now,
                    metrics=snapshot_plan["metrics"],
                    metrics_hash=snapshot_plan["metrics_hash"],
                    snapshot_key=snapshot_plan["snapshot_key"],
                )
            )

    async def _mark_stale(self, import_job_id: UUID, preview_revision: int, reason: str) -> None:
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
