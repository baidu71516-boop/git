"""Targeted repair of committed Huitun source-state and metric projections."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.imports.adapters import HuitunCsvAdapter
from backend_core.imports.contracts import CanonicalInfluencerRecord
from backend_core.imports.enums import (
    ImportJobStatus,
    ImportMatchType,
    ImportRowAction,
    ImportSourceType,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.hashing import hash_document
from backend_core.imports.merge_applier import ImportMergeApplier
from backend_core.imports.models import ImportJob, ImportRow
from backend_core.imports.parsers import RawTabularRecord
from backend_core.imports.planner import ImportPlanner, identity_lock_keys
from backend_core.imports.repository import ImportRepository
from backend_core.influencers.enums import DataSource
from backend_core.influencers.models import InfluencerPlatformAccount

CURRENT_METRIC_ENRICHMENT_FIELDS = ("works_count", "likes_count", "avg_likes")


class HuitunReprojectionService:
    """Reproject one confirmed Huitun job from immutable persisted row payloads."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = ImportRepository(session)
        self.planner = ImportPlanner(self.repository)
        self.applier = ImportMergeApplier(session, repository=self.repository)

    async def reproject(
        self,
        *,
        department_id: UUID,
        import_job_id: UUID,
    ) -> dict[str, Any]:
        """Repair only source-state/current-metric/snapshot projections for one job."""

        try:
            await self.repository.acquire_identity_locks(
                [f"phase2:huitun-reprojection-job:{import_job_id}"]
            )
            job = await self.repository.get_import_job(import_job_id, for_update=True)
            self._require_repairable_job(job, department_id)
            assert job is not None
            assert job.confirmed_revision is not None

            rows = await self.repository.list_committed_huitun_reprojection_rows(
                department_id=department_id,
                import_job_id=job.id,
                confirmed_revision=job.confirmed_revision,
                for_update=True,
            )
            normalized = [(row, self._normalize_stored_row(row)) for row in rows]
            self._require_persisted_row_targets(normalized)
            await self.repository.acquire_identity_locks(
                (
                    *(key for _, record in normalized for key in identity_lock_keys(record)),
                    *(
                        f"phase2:target-account:{row.matched_platform_account_id}"
                        for row, _ in normalized
                    ),
                    *(
                        f"phase2:target-influencer:{row.matched_influencer_id}"
                        for row, _ in normalized
                    ),
                )
            )

            source_states_repaired = 0
            metric_snapshots_inserted = 0
            for row, record in normalized:
                account = await self._require_original_account(row, record)
                projection = await self.planner.plan_existing_account_projections(
                    record=record,
                    account=account,
                )
                applied = await self.applier.reproject_source_state_and_metrics(
                    job,
                    row,
                    record,
                    account,
                    source_state_plan=projection.source_state,
                    metrics_plan=projection.metrics,
                    expected_snapshot=projection.snapshot,
                    captured_at=self._committed_at(row),
                )
                source_states_repaired += int(applied.source_state_repaired)
                metric_snapshots_inserted += int(applied.metric_snapshot_inserted)

            await self.session.commit()
            return {
                "department_id": str(department_id),
                "import_job_id": str(job.id),
                "selected_committed_rows": len(rows),
                "processed": len(rows),
                "source_states_repaired": source_states_repaired,
                "source_states_noop": len(rows) - source_states_repaired,
                "metric_snapshots_inserted": metric_snapshots_inserted,
                "metric_snapshots_noop": len(rows) - metric_snapshots_inserted,
                "conflicts": 0,
                "errors": 0,
            }
        except BaseException:
            await self.session.rollback()
            raise

    async def enrich_current_metrics_missing_only(
        self,
        *,
        department_id: UUID,
        import_job_id: UUID,
        import_row_ids: tuple[UUID, ...] | None = None,
    ) -> dict[str, Any]:
        """Add newly supported metrics from immutable raw Huitun provenance only."""

        try:
            await self.repository.acquire_identity_locks(
                [f"phase2:huitun-current-metrics-enrichment-job:{import_job_id}"]
            )
            job = await self.repository.get_import_job(import_job_id, for_update=True)
            self._require_repairable_job(job, department_id)
            assert job is not None
            assert job.confirmed_revision is not None

            rows = await self.repository.list_committed_huitun_reprojection_rows(
                department_id=department_id,
                import_job_id=job.id,
                confirmed_revision=job.confirmed_revision,
                for_update=True,
            )

            if import_row_ids is not None:
                requested = set(import_row_ids)
                rows = [row for row in rows if row.id in requested]
                found = {row.id for row in rows}
                if found != requested:
                    raise ImportDomainError(
                        "HUITUN_CURRENT_METRICS_ENRICHMENT_ROW_NOT_FOUND",
                        "Requested committed Huitun row was not found",
                        status_code=404,
                    )

            normalized = [(row, self._normalize_stored_row(row)) for row in rows]
            self._require_persisted_row_targets(normalized)

            updated = 0
            noop = 0
            fields_added = 0

            for row, record in normalized:
                account = await self._require_original_account(row, record)
                current = await self.repository.get_current_metrics(
                    account.id,
                    record.source,
                    for_update=True,
                )
                if current is None:
                    raise ImportDomainError(
                        "HUITUN_CURRENT_METRICS_ENRICHMENT_MISSING",
                        "Current metrics projection is missing",
                        status_code=409,
                    )

                if (
                    current.influencer_id != account.influencer_id
                    or current.platform_account_id != account.id
                    or current.source is not record.source
                    or current.last_import_job_id != row.import_job_id
                    or current.last_import_row_id != row.id
                ):
                    raise ImportDomainError(
                        "HUITUN_CURRENT_METRICS_ENRICHMENT_LINEAGE_CONFLICT",
                        "Current metrics no longer has the original Huitun row lineage",
                        status_code=409,
                    )

                incoming = record.as_dict()["metrics"]
                existing = dict(current.metrics)

                incoming_followers = incoming.get("followers_count")
                existing_followers = existing.get("followers_count")
                if (
                    incoming_followers is not None
                    and existing_followers is not None
                    and incoming_followers != existing_followers
                ):
                    raise ImportDomainError(
                        "HUITUN_CURRENT_METRICS_ENRICHMENT_VALUE_CONFLICT",
                        "Existing followers metric differs from immutable Huitun evidence",
                        status_code=409,
                    )

                merged = dict(existing)
                added_for_row = 0

                for field in CURRENT_METRIC_ENRICHMENT_FIELDS:
                    if field not in incoming:
                        continue
                    incoming_value = incoming[field]
                    if field not in merged or merged[field] is None:
                        merged[field] = incoming_value
                        added_for_row += 1
                    elif merged[field] != incoming_value:
                        raise ImportDomainError(
                            "HUITUN_CURRENT_METRICS_ENRICHMENT_VALUE_CONFLICT",
                            f"Existing {field} differs from immutable Huitun evidence",
                            status_code=409,
                        )

                if added_for_row:
                    current.metrics = merged
                    current.metrics_hash = hash_document(merged)
                    updated += 1
                    fields_added += added_for_row
                else:
                    noop += 1

            await self.session.commit()
            return {
                "department_id": str(department_id),
                "import_job_id": str(job.id),
                "selected_committed_rows": len(rows),
                "processed": len(rows),
                "current_metrics_updated": updated,
                "current_metrics_noop": noop,
                "fields_added": fields_added,
                "conflicts": 0,
                "errors": 0,
            }
        except BaseException:
            await self.session.rollback()
            raise

    @staticmethod
    def _require_repairable_job(job: ImportJob | None, department_id: UUID) -> None:
        if job is None or job.department_id != department_id:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found", status_code=404)
        if job.source_type is not ImportSourceType.MANUAL_HUITUN_EXPORT:
            raise ImportDomainError(
                "HUITUN_REPROJECTION_SOURCE_INVALID",
                "Only Huitun import jobs can be reprojected",
                status_code=409,
            )
        if (
            job.status is not ImportJobStatus.COMPLETED
            or job.confirmed_revision is None
            or job.confirmed_at is None
            or job.completed_at is None
        ):
            raise ImportDomainError(
                "HUITUN_REPROJECTION_STATE_INVALID",
                "Import job is not a completed confirmed import",
                status_code=409,
            )

    @staticmethod
    def _normalize_stored_row(row: ImportRow) -> CanonicalInfluencerRecord:
        raw_data = row.raw_data
        if not isinstance(raw_data, dict) or "__raw_values__" in raw_data:
            raise ImportDomainError(
                "HUITUN_REPROJECTION_RAW_DATA_INVALID",
                "Committed import row cannot be safely normalized",
                status_code=409,
            )
        values: dict[str, str] = {}
        for header, value in raw_data.items():
            if not isinstance(header, str) or not isinstance(value, str):
                raise ImportDomainError(
                    "HUITUN_REPROJECTION_RAW_DATA_INVALID",
                    "Committed import row cannot be safely normalized",
                    status_code=409,
                )
            values[header] = value
        if not values:
            raise ImportDomainError(
                "HUITUN_REPROJECTION_RAW_DATA_INVALID",
                "Committed import row cannot be safely normalized",
                status_code=409,
            )

        raw_record = RawTabularRecord(
            row_number=row.row_number,
            raw_data=dict(raw_data),
            values=values,
        )
        adapter = HuitunCsvAdapter()
        adapter.mapping_for_headers(list(values))
        adapter.validate_table([raw_record])
        adapted = adapter.adapt(raw_record)
        if (
            adapted.errors
            or adapted.record.errors
            or adapted.record.source is not DataSource.HUITUN
        ):
            raise ImportDomainError(
                "HUITUN_REPROJECTION_NORMALIZATION_FAILED",
                "Committed import row cannot be safely normalized",
                status_code=409,
            )
        return adapted.record

    @staticmethod
    def _require_persisted_row_targets(
        rows: list[tuple[ImportRow, CanonicalInfluencerRecord]],
    ) -> None:
        for row, _ in rows:
            if row.matched_influencer_id is None or row.matched_platform_account_id is None:
                raise ImportDomainError(
                    "HUITUN_REPROJECTION_IDENTITY_CONFLICT",
                    "Committed import row has no persisted identity target",
                    status_code=409,
                )
            if (
                row.committed_action is not ImportRowAction.CREATE
                and row.match_type is ImportMatchType.NONE
            ):
                raise ImportDomainError(
                    "HUITUN_REPROJECTION_IDENTITY_CONFLICT",
                    "Committed import row has no safe identity provenance",
                    status_code=409,
                )

    async def _require_original_account(
        self,
        row: ImportRow,
        record: CanonicalInfluencerRecord,
    ) -> InfluencerPlatformAccount:
        assert row.matched_platform_account_id is not None
        assert row.matched_influencer_id is not None
        account = await self.repository.get_platform_account(row.matched_platform_account_id)
        if (
            account is None
            or account.influencer_id != row.matched_influencer_id
            or account.platform is not record.platform_identity.platform
            or not await self._has_persisted_identity(row, record, account)
        ):
            raise ImportDomainError(
                "HUITUN_REPROJECTION_IDENTITY_CONFLICT",
                "Committed import row no longer has its original identity target",
                status_code=409,
            )
        snapshot = await self.repository.get_metric_snapshot_for_import_row(row.id, for_update=True)
        if snapshot is not None and (
            snapshot.import_job_id != row.import_job_id
            or snapshot.influencer_id != account.influencer_id
            or snapshot.platform_account_id != account.id
            or snapshot.source is not record.source
        ):
            raise ImportDomainError(
                "HUITUN_REPROJECTION_IDENTITY_CONFLICT",
                "Committed metric snapshot no longer has its original identity target",
                status_code=409,
            )
        return account

    async def _has_persisted_identity(
        self,
        row: ImportRow,
        record: CanonicalInfluencerRecord,
        account: InfluencerPlatformAccount,
    ) -> bool:
        """Validate every persisted/raw hard identity without matching anew."""

        identity = record.platform_identity
        if (
            identity.platform_account_id is not None
            and account.platform_account_id != identity.platform_account_id
        ):
            return False
        if (
            identity.normalized_profile_url is not None
            and account.normalized_profile_url != identity.normalized_profile_url
        ):
            return False
        if identity.external_source_id is not None and (
            await self.repository.get_source_identity(
                account.id,
                record.source,
                identity.external_source_id,
            )
            is None
        ):
            return False

        if row.match_type is ImportMatchType.PLATFORM_ACCOUNT_ID:
            return identity.platform_account_id is not None
        if row.match_type is ImportMatchType.EXTERNAL_SOURCE_ID:
            return identity.external_source_id is not None
        if row.match_type is ImportMatchType.NORMALIZED_PROFILE_URL:
            return identity.normalized_profile_url is not None
        if row.committed_action is not ImportRowAction.CREATE:
            return False
        return any(
            (
                identity.platform_account_id is not None,
                identity.external_source_id is not None,
                identity.normalized_profile_url is not None,
            )
        )

    @staticmethod
    def _committed_at(row: ImportRow) -> datetime:
        if row.committed_at is None:
            raise ImportDomainError(
                "HUITUN_REPROJECTION_STATE_INVALID",
                "Committed import row is missing its commit timestamp",
                status_code=409,
            )
        if row.committed_at.tzinfo is None:
            return row.committed_at.replace(tzinfo=UTC)
        return row.committed_at.astimezone(UTC)


__all__ = ["HuitunReprojectionService"]
