"""Persistence operations for collection and two-phase import services."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.imports.enums import (
    ImportJobFileStatus,
    ImportJobStatus,
    ImportRowAction,
    SourceAcquiredAtOrigin,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.hashing import advisory_lock_key
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportJobFileClientId,
    ImportRow,
    StoredImportFile,
)
from backend_core.influencers.enums import ContactType, DataSource, Platform
from backend_core.influencers.models import (
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)


@dataclass(frozen=True, slots=True)
class ImportJobFileRecord:
    occurrence: ImportJobFile
    stored_file: StoredImportFile


class ImportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_collection_job(
        self, collection_job_id: UUID, *, for_update: bool = False
    ) -> CollectionJob | None:
        statement = select(CollectionJob).where(CollectionJob.id == collection_job_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(CollectionJob | None, await self.session.scalar(statement))

    async def list_collection_jobs(self, department_id: UUID | None) -> list[CollectionJob]:
        statement = select(CollectionJob)
        if department_id is not None:
            statement = statement.where(CollectionJob.department_id == department_id)
        result = await self.session.scalars(statement.order_by(CollectionJob.created_at.desc()))
        return list(result)

    async def get_import_job(
        self, import_job_id: UUID, *, for_update: bool = False
    ) -> ImportJob | None:
        statement = select(ImportJob).where(ImportJob.id == import_job_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(ImportJob | None, await self.session.scalar(statement))

    async def list_import_jobs(self, department_id: UUID | None) -> list[ImportJob]:
        statement = select(ImportJob)
        if department_id is not None:
            statement = statement.where(ImportJob.department_id == department_id)
        result = await self.session.scalars(statement.order_by(ImportJob.created_at.desc()))
        return list(result)

    async def get_stored_file(self, stored_file_id: UUID) -> StoredImportFile | None:
        return await self.session.get(StoredImportFile, stored_file_id)

    async def get_stored_file_by_sha256(self, sha256: str) -> StoredImportFile | None:
        return cast(
            StoredImportFile | None,
            await self.session.scalar(
                select(StoredImportFile).where(StoredImportFile.sha256 == sha256)
            ),
        )

    async def get_import_job_file(
        self, import_job_file_id: UUID, *, for_update: bool = False
    ) -> ImportJobFile | None:
        statement = select(ImportJobFile).where(ImportJobFile.id == import_job_file_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(ImportJobFile | None, await self.session.scalar(statement))

    async def get_platform_account(
        self,
        platform_account_id: UUID,
    ) -> InfluencerPlatformAccount | None:
        return cast(
            InfluencerPlatformAccount | None,
            await self.session.get(InfluencerPlatformAccount, platform_account_id),
        )

    async def get_import_job_file_record(
        self,
        import_job_id: UUID,
        import_job_file_id: UUID,
    ) -> ImportJobFileRecord | None:
        row = (
            await self.session.execute(
                select(ImportJobFile, StoredImportFile)
                .join(StoredImportFile, StoredImportFile.id == ImportJobFile.stored_file_id)
                .where(
                    ImportJobFile.id == import_job_file_id,
                    ImportJobFile.import_job_id == import_job_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        occurrence, stored_file = row
        return ImportJobFileRecord(occurrence, stored_file)

    async def get_import_job_file_by_stored_file(
        self,
        import_job_id: UUID,
        stored_file_id: UUID,
    ) -> ImportJobFile | None:
        return cast(
            ImportJobFile | None,
            await self.session.scalar(
                select(ImportJobFile).where(
                    ImportJobFile.import_job_id == import_job_id,
                    ImportJobFile.stored_file_id == stored_file_id,
                )
            ),
        )

    async def get_import_job_file_by_sha256(
        self,
        import_job_id: UUID,
        sha256: str,
    ) -> ImportJobFileRecord | None:
        row = (
            await self.session.execute(
                select(ImportJobFile, StoredImportFile)
                .join(StoredImportFile, StoredImportFile.id == ImportJobFile.stored_file_id)
                .where(
                    ImportJobFile.import_job_id == import_job_id,
                    StoredImportFile.sha256 == sha256,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        occurrence, stored_file = row
        return ImportJobFileRecord(occurrence, stored_file)

    async def get_file_client_id_alias(
        self,
        import_job_id: UUID,
        client_file_id: str,
    ) -> ImportJobFileRecord | None:
        row = (
            await self.session.execute(
                select(ImportJobFile, StoredImportFile)
                .join(
                    ImportJobFileClientId,
                    ImportJobFileClientId.import_job_file_id == ImportJobFile.id,
                )
                .join(StoredImportFile, StoredImportFile.id == ImportJobFile.stored_file_id)
                .where(
                    ImportJobFileClientId.import_job_id == import_job_id,
                    ImportJobFileClientId.client_file_id == client_file_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        occurrence, stored_file = row
        return ImportJobFileRecord(occurrence, stored_file)

    async def has_other_job_file_reference(
        self,
        stored_file_id: UUID,
        import_job_id: UUID,
    ) -> bool:
        value = await self.session.scalar(
            select(ImportJobFile.id).where(
                ImportJobFile.stored_file_id == stored_file_id,
                ImportJobFile.import_job_id != import_job_id,
            )
        )
        return value is not None

    async def import_job_file_usage(self, import_job_id: UUID) -> tuple[int, int, int]:
        count, total_bytes, max_position = (
            await self.session.execute(
                select(
                    func.count(ImportJobFile.id),
                    func.coalesce(func.sum(StoredImportFile.size), 0),
                    func.coalesce(func.max(ImportJobFile.position), 0),
                )
                .select_from(ImportJobFile)
                .join(StoredImportFile, StoredImportFile.id == ImportJobFile.stored_file_id)
                .where(ImportJobFile.import_job_id == import_job_id)
            )
        ).one()
        return int(count), int(total_bytes), int(max_position)

    async def list_import_job_file_records(
        self,
        import_job_id: UUID,
    ) -> list[ImportJobFileRecord]:
        rows = list(
            (
                await self.session.execute(
                    select(ImportJobFile, StoredImportFile)
                    .join(StoredImportFile, StoredImportFile.id == ImportJobFile.stored_file_id)
                    .where(ImportJobFile.import_job_id == import_job_id)
                    .order_by(ImportJobFile.position, ImportJobFile.id)
                )
            ).all()
        )
        if not rows:
            return []
        return [ImportJobFileRecord(occurrence, stored_file) for occurrence, stored_file in rows]

    async def list_import_job_files(
        self, import_job_id: UUID, *, for_update: bool = False
    ) -> list[ImportJobFile]:
        statement = (
            select(ImportJobFile)
            .where(ImportJobFile.import_job_id == import_job_id)
            .order_by(ImportJobFile.position, ImportJobFile.id)
        )
        if for_update:
            statement = statement.with_for_update()
        return list(await self.session.scalars(statement))

    async def get_legacy_import_job_file(
        self, job: ImportJob, *, for_update: bool = False
    ) -> ImportJobFile:
        files = await self.list_import_job_files(job.id, for_update=for_update)
        if len(files) != 1:
            raise ImportDomainError(
                "IMPORT_FILE_OCCURRENCE_INVALID",
                "Legacy import job must have exactly one file occurrence",
            )
        occurrence = files[0]
        if (
            job.stored_file_id is None
            or occurrence.stored_file_id != job.stored_file_id
            or occurrence.position != 1
            or occurrence.source_acquired_at is not None
            or occurrence.source_acquired_at_origin is not SourceAcquiredAtOrigin.LEGACY_UNKNOWN
        ):
            raise ImportDomainError(
                "IMPORT_FILE_OCCURRENCE_INVALID",
                "Legacy import file occurrence does not match the import job",
            )
        return occurrence

    async def list_import_rows(
        self,
        import_job_id: UUID,
        *,
        offset: int = 0,
        limit: int = 100,
        action: ImportRowAction | None = None,
    ) -> tuple[list[ImportRow], int]:
        criteria = [ImportRow.import_job_id == import_job_id]
        if action is not None:
            criteria.append(ImportRow.action == action)
        visible_staging = or_(
            ImportJob.status != ImportJobStatus.DRAFT,
            ImportJobFile.status == ImportJobFileStatus.READY,
        )
        count = await self.session.scalar(
            select(func.count())
            .select_from(ImportRow)
            .join(ImportJobFile, ImportJobFile.id == ImportRow.import_job_file_id)
            .join(ImportJob, ImportJob.id == ImportRow.import_job_id)
            .where(*criteria, visible_staging)
        )
        result = await self.session.scalars(
            select(ImportRow)
            .join(
                ImportJobFile,
                ImportJobFile.id == ImportRow.import_job_file_id,
            )
            .join(ImportJob, ImportJob.id == ImportRow.import_job_id)
            .where(*criteria, visible_staging)
            .order_by(ImportJobFile.position, ImportRow.row_number, ImportRow.id)
            .offset(offset)
            .limit(limit)
        )
        return list(result), int(count or 0)

    async def all_import_rows(self, import_job_id: UUID) -> list[ImportRow]:
        result = await self.session.scalars(
            select(ImportRow)
            .join(
                ImportJobFile,
                ImportJobFile.id == ImportRow.import_job_file_id,
            )
            .join(ImportJob, ImportJob.id == ImportRow.import_job_id)
            .where(
                ImportRow.import_job_id == import_job_id,
                or_(
                    ImportJob.status != ImportJobStatus.DRAFT,
                    ImportJobFile.status == ImportJobFileStatus.READY,
                ),
            )
            .order_by(ImportJobFile.position, ImportRow.row_number, ImportRow.id)
        )
        return list(result)

    async def list_batch_staging_rows(self, import_job_id: UUID) -> list[ImportRow]:
        """Return only rows belonging to included, successfully parsed occurrences."""

        result = await self.session.scalars(
            select(ImportRow)
            .join(ImportJobFile, ImportJobFile.id == ImportRow.import_job_file_id)
            .where(
                ImportRow.import_job_id == import_job_id,
                ImportJobFile.status == ImportJobFileStatus.READY,
            )
            .order_by(ImportJobFile.position, ImportRow.row_number, ImportRow.id)
        )
        return list(result)

    async def delete_import_rows_for_file(self, import_job_file_id: UUID) -> None:
        await self.session.execute(
            delete(ImportRow).where(ImportRow.import_job_file_id == import_job_file_id)
        )

    async def accounts_by_platform_id(
        self, platform: Platform, platform_account_id: str
    ) -> list[InfluencerPlatformAccount]:
        result = await self.session.scalars(
            select(InfluencerPlatformAccount).where(
                InfluencerPlatformAccount.platform == platform,
                InfluencerPlatformAccount.platform_account_id == platform_account_id,
            )
        )
        return list(result)

    async def accounts_by_profile_url(
        self, platform: Platform, normalized_profile_url: str
    ) -> list[InfluencerPlatformAccount]:
        result = await self.session.scalars(
            select(InfluencerPlatformAccount).where(
                InfluencerPlatformAccount.platform == platform,
                InfluencerPlatformAccount.normalized_profile_url == normalized_profile_url,
            )
        )
        return list(result)

    async def accounts_by_external_id(
        self,
        platform: Platform,
        source: DataSource,
        external_account_id: str,
    ) -> list[InfluencerPlatformAccount]:
        result = await self.session.scalars(
            select(InfluencerPlatformAccount)
            .join(
                PlatformAccountSourceIdentity,
                PlatformAccountSourceIdentity.platform_account_id == InfluencerPlatformAccount.id,
            )
            .where(
                PlatformAccountSourceIdentity.platform == platform,
                PlatformAccountSourceIdentity.source == source,
                PlatformAccountSourceIdentity.external_account_id == external_account_id,
            )
        )
        return list(result)

    async def get_source_identity(
        self,
        platform_account_id: UUID,
        source: DataSource,
        external_account_id: str,
    ) -> PlatformAccountSourceIdentity | None:
        return cast(
            PlatformAccountSourceIdentity | None,
            await self.session.scalar(
                select(PlatformAccountSourceIdentity).where(
                    PlatformAccountSourceIdentity.platform_account_id == platform_account_id,
                    PlatformAccountSourceIdentity.source == source,
                    PlatformAccountSourceIdentity.external_account_id == external_account_id,
                )
            ),
        )

    async def accounts_by_handle(
        self, platform: Platform, account_handle: str
    ) -> list[InfluencerPlatformAccount]:
        result = await self.session.scalars(
            select(InfluencerPlatformAccount).where(
                InfluencerPlatformAccount.platform == platform,
                InfluencerPlatformAccount.account_handle == account_handle,
            )
        )
        return list(result)

    async def get_source_state(
        self, platform_account_id: UUID, source: DataSource
    ) -> InfluencerSourceState | None:
        return cast(
            InfluencerSourceState | None,
            await self.session.scalar(
                select(InfluencerSourceState).where(
                    InfluencerSourceState.platform_account_id == platform_account_id,
                    InfluencerSourceState.source == source,
                )
            ),
        )

    async def get_current_metrics(
        self, platform_account_id: UUID, source: DataSource
    ) -> InfluencerCurrentMetrics | None:
        return cast(
            InfluencerCurrentMetrics | None,
            await self.session.scalar(
                select(InfluencerCurrentMetrics).where(
                    InfluencerCurrentMetrics.platform_account_id == platform_account_id,
                    InfluencerCurrentMetrics.source == source,
                )
            ),
        )

    async def source_contacts(
        self,
        influencer_id: UUID,
        source: DataSource,
        contact_type: ContactType,
    ) -> list[InfluencerContact]:
        result = await self.session.scalars(
            select(InfluencerContact).where(
                InfluencerContact.influencer_id == influencer_id,
                InfluencerContact.source == source,
                InfluencerContact.type == contact_type,
            )
        )
        return list(result)

    async def contacts_with_normalized_value(
        self, contact_type: ContactType, normalized_value: str
    ) -> list[InfluencerContact]:
        result = await self.session.scalars(
            select(InfluencerContact).where(
                InfluencerContact.type == contact_type,
                InfluencerContact.normalized_value == normalized_value,
            )
        )
        return list(result)

    async def snapshot_exists(self, snapshot_key: str) -> bool:
        value = await self.session.scalar(
            select(InfluencerMetricSnapshot.id).where(
                InfluencerMetricSnapshot.snapshot_key == snapshot_key
            )
        )
        return value is not None

    async def acquire_identity_locks(self, identities: Iterable[str]) -> None:
        if self.session.bind is None or self.session.bind.dialect.name != "postgresql":
            return
        keys = sorted({advisory_lock_key(identity) for identity in identities})
        for key in keys:
            await self.session.execute(select(func.pg_advisory_xact_lock(key)))
