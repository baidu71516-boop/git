"""Apply the one frozen Import Planner merge contract to ORM state.

The Planner owns every merge decision.  This module is deliberately limited to
materialising that already-validated plan.  Legacy Confirm may use repository
lookups, while bulk Confirm supplies a strict prefetch cache so applying rows
does not issue row-shaped SELECTs or require intermediate flushes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.imports.bulk_repository import AccountSourceKey, PrefetchedImportState
from backend_core.imports.contracts import CanonicalInfluencerRecord
from backend_core.imports.enums import ImportRowAction
from backend_core.imports.models import ImportJob, ImportRow
from backend_core.imports.planner import PlannedImportRow
from backend_core.imports.repository import ImportRepository
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
    """The persisted plan can no longer be applied to current database state."""


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(slots=True)
class MergeApplyCache:
    """Mutable write view built from one frozen bulk prefetch.

    The cache is strict: a plan that references an account, Contact, or covered
    account/source entity absent from the supplied prefetch is stale.  It never
    falls back to a database read.  Copies are intentional because applying a
    plan updates the same ORM objects and may add explicitly identified objects
    for later rows in the transaction.
    """

    accounts_by_id: dict[UUID, InfluencerPlatformAccount]
    source_states: dict[AccountSourceKey, InfluencerSourceState]
    current_metrics: dict[AccountSourceKey, InfluencerCurrentMetrics]
    contacts_by_id: dict[UUID, InfluencerContact]
    covered_account_sources: set[AccountSourceKey]

    @classmethod
    def from_prefetched(cls, state: PrefetchedImportState) -> MergeApplyCache:
        contacts = {
            contact.id: contact
            for groups in (
                state.source_contacts.values(),
                state.contacts_by_normalized_value.values(),
            )
            for group in groups
            for contact in group
        }
        return cls(
            accounts_by_id=dict(state.accounts_by_id),
            source_states=dict(state.source_states),
            current_metrics=dict(state.current_metrics),
            contacts_by_id=contacts,
            covered_account_sources=set(state.covered_account_sources),
        )


@dataclass(slots=True)
class _PendingCreate:
    job: ImportJob
    row: ImportRow
    record: CanonicalInfluencerRecord
    account: InfluencerPlatformAccount
    merge: dict[str, Any]
    now: datetime


class ImportMergeApplier:
    """Materialise Planner output without owning merge policy."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        repository: ImportRepository | None = None,
        cache: MergeApplyCache | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or ImportRepository(session)
        self.cache = cache
        self._pending_creates: list[_PendingCreate] = []

    async def apply(
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
                id=uuid4(),
                display_name=create["display_name"],
                owner_operator_id=job.operator_id,
                crm_stage=CRMStage.TO_DEVELOP,
            )
            public_profile = create["public_profile"]
            account = InfluencerPlatformAccount(
                id=uuid4(),
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
            self.session.add_all((influencer, account))
            if self.cache is not None:
                self.cache.accounts_by_id[account.id] = account
                self.cache.covered_account_sources.add(AccountSourceKey(account.id, record.source))
            # All new parent rows are flushed as one phase before any child
            # entity referencing their composite account/influencer key is
            # staged. SQLAlchemy has no relationships for these plan-driven
            # edges, so relying on unit-of-work ordering alone is unsafe.
            self._pending_creates.append(_PendingCreate(job, row, record, account, merge, now))
            row.committed_action = plan.action
            row.committed_at = now
            return
        else:
            if plan.matched_platform_account_id is None:
                raise PreviewStaleError("matched_account_missing")
            account = await self._account(plan.matched_platform_account_id)
            for field, value in merge["account_updates"].items():
                setattr(account, field, value)

        row.matched_influencer_id = account.influencer_id
        row.matched_platform_account_id = account.id
        await self._apply_account_plan(job, row, record, account, merge, now)
        row.committed_action = plan.action
        row.committed_at = now

    async def _apply_account_plan(
        self,
        job: ImportJob,
        row: ImportRow,
        record: CanonicalInfluencerRecord,
        account: InfluencerPlatformAccount,
        merge: dict[str, Any],
        now: datetime,
    ) -> None:
        source_identity_plan = merge.get("source_identity")
        if source_identity_plan:
            self.session.add(
                PlatformAccountSourceIdentity(
                    id=uuid4(),
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
            source_state = await self._source_state(account.id, record.source)
            if source_state is None:
                source_state = InfluencerSourceState(
                    id=uuid4(),
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
                if self.cache is not None:
                    self.cache.source_states[AccountSourceKey(account.id, record.source)] = (
                        source_state
                    )
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

    async def finalize(self) -> None:
        """Flush new business rows once, then make CREATE lineage visible.

        Bulk callers invoke this once after applying the complete batch.  The
        Legacy compatibility delegate invokes it per row, preserving its prior
        flush timing without imposing row-shaped flushes on bulk Confirm.
        """

        if not self._pending_creates:
            return
        pending = tuple(self._pending_creates)
        self._pending_creates.clear()
        await self.session.flush()
        for item in pending:
            item.row.matched_influencer_id = item.account.influencer_id
            item.row.matched_platform_account_id = item.account.id
            await self._apply_account_plan(
                item.job,
                item.row,
                item.record,
                item.account,
                item.merge,
                item.now,
            )

    async def _account(self, account_id: UUID) -> InfluencerPlatformAccount:
        if self.cache is not None:
            account = self.cache.accounts_by_id.get(account_id)
        else:
            account = await self.session.get(InfluencerPlatformAccount, account_id)
        if account is None:
            raise PreviewStaleError("matched_account_deleted")
        return account

    async def _source_state(
        self, account_id: UUID, source: DataSource
    ) -> InfluencerSourceState | None:
        key = AccountSourceKey(account_id, source)
        if self.cache is None:
            return await self.repository.get_source_state(account_id, source)
        if key not in self.cache.covered_account_sources:
            raise PreviewStaleError("source_state_not_prefetched")
        return self.cache.source_states.get(key)

    async def _contact(self, contact_id: UUID) -> InfluencerContact | None:
        if self.cache is not None:
            contact = self.cache.contacts_by_id.get(contact_id)
            if contact is None:
                raise PreviewStaleError("contact_deleted")
            return contact
        else:
            # Preserve the Phase 1B apply behaviour.  Legacy validation normally
            # catches deletion by plan hash before this point, while a deletion
            # in the final race window was historically ignored.
            return await self.session.get(InfluencerContact, contact_id)

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
            contact = await self._contact(UUID(str(contact_id)))
            if contact is not None and contact.source != DataSource.MANUAL:
                contact.is_current = False
        for contact_id in contacts_plan["mark_duplicate_ids"]:
            contact = await self._contact(UUID(str(contact_id)))
            if contact is not None and contact.source != DataSource.MANUAL:
                contact.possible_duplicate_contact = True
        for contact_id in contacts_plan["observe_ids"]:
            contact = await self._contact(UUID(str(contact_id)))
            if contact is not None and contact.source != DataSource.MANUAL:
                contact.last_seen_at = now
                contact.last_import_job_id = job.id
                contact.last_import_row_id = row.id
        for item in contacts_plan["create"]:
            contact = InfluencerContact(
                id=uuid4(),
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
            self.session.add(contact)
            if self.cache is not None:
                self.cache.contacts_by_id[contact.id] = contact

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
            current = await self._current_metrics(account.id, record.source)
            if current is None:
                current = InfluencerCurrentMetrics(
                    id=uuid4(),
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
                if self.cache is not None:
                    self.cache.current_metrics[AccountSourceKey(account.id, record.source)] = (
                        current
                    )
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
                    id=uuid4(),
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

    async def _current_metrics(
        self, account_id: UUID, source: DataSource
    ) -> InfluencerCurrentMetrics | None:
        key = AccountSourceKey(account_id, source)
        if self.cache is None:
            return await self.repository.get_current_metrics(account_id, source)
        if key not in self.cache.covered_account_sources:
            raise PreviewStaleError("current_metrics_not_prefetched")
        return self.cache.current_metrics.get(key)


__all__ = ["ImportMergeApplier", "MergeApplyCache", "PreviewStaleError"]
