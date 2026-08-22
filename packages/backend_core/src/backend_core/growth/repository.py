"""Set-based persistence and canonical fact assembly for deterministic targeting."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend_core.auth.models import Operator
from backend_core.growth.enums import CandidatePoolKind, CandidateResult, Phase3AOperationScope
from backend_core.growth.models import (
    CandidatePool,
    CandidatePoolMember,
    CandidatePoolRun,
    Phase3AIdempotencyRecord,
    TargetingPolicy,
)
from backend_core.growth.targeting import (
    BuyerTargetingPolicy,
    CandidateFactBundle,
    CollectionContextSnapshot,
    SellerTargetingPolicy,
    TargetingEvaluation,
)
from backend_core.imports.enums import ImportJobFileStatus, ImportJobStatus, ImportRowAction
from backend_core.imports.models import CollectionJob, ImportJob, ImportJobFile, ImportRow
from backend_core.influencers.enums import ContactType, Platform
from backend_core.influencers.freshness import FreshnessPolicy
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerPlatformAccount,
    InfluencerSourceState,
)
from backend_core.influencers.repository import (
    eligible_huitun_freshness,
    visible_influencer_criteria,
)

DEFAULT_BATCH_SIZE = 500
MAX_BATCH_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class CandidatePoolPage:
    items: tuple[CandidatePoolOwnerRecord, ...]
    next_cursor: UUID | None


@dataclass(frozen=True, slots=True)
class CandidateRunMemberPage:
    items: tuple[CandidatePoolMemberProjectionRecord, ...]
    next_cursor: UUID | None


@dataclass(frozen=True, slots=True)
class CandidateRunPage:
    items: tuple[CandidatePoolRun, ...]
    next_cursor: UUID | None


@dataclass(frozen=True, slots=True)
class CandidatePoolOwnerRecord:
    """One Candidate Pool with its required Department-local canonical owner."""

    pool: CandidatePool
    owner: Operator


@dataclass(frozen=True, slots=True)
class CandidatePoolMemberProjectionRecord:
    """One historical Candidate Member with safe current canonical identity."""

    member: CandidatePoolMember
    influencer: Influencer
    platform_account: InfluencerPlatformAccount


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _committed_provenance_predicate(
    account_id: ColumnElement[UUID],
    collection_job_id: UUID,
) -> ColumnElement[bool]:
    committed_actions = (
        ImportRowAction.CREATE,
        ImportRowAction.UPDATE,
        ImportRowAction.NO_CHANGE,
    )
    return cast(
        ColumnElement[bool],
        exists(
            select(1)
            .select_from(ImportRow)
            .join(ImportJob, ImportJob.id == ImportRow.import_job_id)
            .join(
                ImportJobFile,
                and_(
                    ImportJobFile.id == ImportRow.import_job_file_id,
                    ImportJobFile.import_job_id == ImportRow.import_job_id,
                ),
            )
            .where(
                ImportRow.matched_platform_account_id == account_id,
                ImportRow.committed_at.is_not(None),
                ImportRow.committed_action.in_(committed_actions),
                ImportJob.collection_job_id == collection_job_id,
                ImportJob.status == ImportJobStatus.COMPLETED,
                ImportJob.confirmed_revision.is_not(None),
                ImportRow.preview_revision == ImportJob.confirmed_revision,
                ImportJobFile.status == ImportJobFileStatus.READY,
            )
        ),
    )


class CandidatePoolRepository:
    """Repository with bounded query counts and no ORM relationship loading."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_phase3a_idempotency_record(
        self,
        *,
        department_id: UUID,
        operation_scope: Phase3AOperationScope,
        idempotency_key: str,
    ) -> Phase3AIdempotencyRecord | None:
        statement = select(Phase3AIdempotencyRecord).where(
            Phase3AIdempotencyRecord.department_id == department_id,
            Phase3AIdempotencyRecord.operation_scope == operation_scope,
            Phase3AIdempotencyRecord.idempotency_key == idempotency_key,
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_pool(
        self,
        pool_id: UUID,
        *,
        department_id: UUID | None,
        for_update: bool = False,
    ) -> CandidatePool | None:
        statement = select(CandidatePool).where(CandidatePool.id == pool_id)
        if department_id is not None:
            statement = statement.where(CandidatePool.department_id == department_id)
        if for_update:
            # A caller may have preflighted this Pool before waiting on the
            # write lock. Refresh the identity-map row with the lock-acquired
            # database state so version/current-policy updates cannot clobber
            # another committed writer.
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_pool_with_owner(
        self,
        pool_id: UUID,
        *,
        department_id: UUID,
    ) -> CandidatePoolOwnerRecord | None:
        """Return a scoped Pool and owner without excluding disabled owners."""

        row = (
            await self.session.execute(
                select(CandidatePool, Operator)
                .join(
                    Operator,
                    and_(
                        Operator.id == CandidatePool.owner_operator_id,
                        Operator.department_id == CandidatePool.department_id,
                    ),
                )
                .where(
                    CandidatePool.id == pool_id,
                    CandidatePool.department_id == department_id,
                )
            )
        ).first()
        if row is None:
            return None
        pool, owner = row
        return CandidatePoolOwnerRecord(pool=pool, owner=owner)

    async def list_pools(
        self,
        *,
        department_id: UUID | None,
        cursor: UUID | None,
        limit: int,
    ) -> CandidatePoolPage:
        statement = select(CandidatePool, Operator).join(
            Operator,
            and_(
                Operator.id == CandidatePool.owner_operator_id,
                Operator.department_id == CandidatePool.department_id,
            ),
        )
        if department_id is not None:
            statement = statement.where(CandidatePool.department_id == department_id)
        if cursor is not None:
            statement = statement.where(CandidatePool.id > cursor)
        statement = statement.order_by(CandidatePool.id).limit(limit + 1)
        rows = tuple(await self.session.execute(statement))
        items = tuple(
            CandidatePoolOwnerRecord(pool=pool, owner=owner) for pool, owner in rows[:limit]
        )
        return CandidatePoolPage(
            items=items,
            next_cursor=items[-1].pool.id if len(rows) > limit and items else None,
        )

    async def get_policy(
        self,
        policy_id: UUID,
        *,
        pool_id: UUID,
        for_update: bool = False,
    ) -> TargetingPolicy | None:
        statement = select(TargetingPolicy).where(
            TargetingPolicy.id == policy_id,
            TargetingPolicy.pool_id == pool_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_current_policy(
        self,
        pool: CandidatePool,
        *,
        for_update: bool = False,
    ) -> TargetingPolicy | None:
        if pool.current_policy_id is None:
            return None
        return await self.get_policy(pool.current_policy_id, pool_id=pool.id, for_update=for_update)

    async def next_policy_version(self, pool_id: UUID) -> int:
        current = await self.session.scalar(
            select(func.coalesce(func.max(TargetingPolicy.version), 0)).where(
                TargetingPolicy.pool_id == pool_id
            )
        )
        return int(current or 0) + 1

    async def list_policies(self, pool_id: UUID) -> tuple[TargetingPolicy, ...]:
        statement = (
            select(TargetingPolicy)
            .where(TargetingPolicy.pool_id == pool_id)
            .order_by(TargetingPolicy.version)
        )
        return tuple((await self.session.execute(statement)).scalars())

    async def get_run(
        self,
        run_id: UUID,
        *,
        pool_id: UUID,
        for_update: bool = False,
    ) -> CandidatePoolRun | None:
        statement = select(CandidatePoolRun).where(
            CandidatePoolRun.id == run_id,
            CandidatePoolRun.pool_id == pool_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_run_any(
        self,
        run_id: UUID,
        *,
        for_update: bool = False,
    ) -> CandidatePoolRun | None:
        statement = select(CandidatePoolRun).where(CandidatePoolRun.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_run_by_idempotency_key(
        self,
        *,
        pool_id: UUID,
        idempotency_key: str,
        for_update: bool = False,
    ) -> CandidatePoolRun | None:
        statement = select(CandidatePoolRun).where(
            CandidatePoolRun.pool_id == pool_id,
            CandidatePoolRun.idempotency_key == idempotency_key,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_runs(
        self,
        *,
        pool_id: UUID,
        cursor: UUID | None,
        limit: int,
    ) -> CandidateRunPage:
        statement = select(CandidatePoolRun).where(CandidatePoolRun.pool_id == pool_id)
        if cursor is not None:
            statement = statement.where(CandidatePoolRun.id > cursor)
        statement = statement.order_by(CandidatePoolRun.id).limit(limit + 1)
        rows = tuple((await self.session.execute(statement)).scalars())
        return CandidateRunPage(
            items=rows[:limit],
            next_cursor=rows[limit - 1].id if len(rows) > limit else None,
        )

    async def list_run_members(
        self,
        *,
        pool_id: UUID,
        department_id: UUID,
        run_id: UUID,
        cursor: UUID | None,
        limit: int,
        result: CandidateResult | None = None,
    ) -> CandidateRunMemberPage:
        # NOT_MATCH is counted on the run but never materialized as a public
        # row. Keep the invariant in the read path as well for legacy/corrupt
        # rows that may predate the materializer guard.
        statement = (
            select(CandidatePoolMember, Influencer, InfluencerPlatformAccount)
            .select_from(CandidatePoolMember)
            .join(
                CandidatePoolRun,
                and_(
                    CandidatePoolRun.id == CandidatePoolMember.run_id,
                    CandidatePoolRun.pool_id == pool_id,
                ),
            )
            .join(
                CandidatePool,
                and_(
                    CandidatePool.id == CandidatePoolRun.pool_id,
                    CandidatePool.department_id == department_id,
                ),
            )
            .join(Influencer, Influencer.id == CandidatePoolMember.influencer_id)
            .join(
                InfluencerPlatformAccount,
                and_(
                    InfluencerPlatformAccount.id == CandidatePoolMember.platform_account_id,
                    InfluencerPlatformAccount.influencer_id == CandidatePoolMember.influencer_id,
                ),
            )
            .where(
                CandidatePoolMember.run_id == run_id,
                CandidatePoolMember.result.in_((CandidateResult.MATCH, CandidateResult.UNKNOWN)),
            )
        )
        if result is not None:
            statement = statement.where(CandidatePoolMember.result == result)
        if cursor is not None:
            statement = statement.where(CandidatePoolMember.id > cursor)
        statement = statement.order_by(CandidatePoolMember.id).limit(limit + 1)
        rows = tuple(await self.session.execute(statement))
        items = tuple(
            CandidatePoolMemberProjectionRecord(
                member=member,
                influencer=influencer,
                platform_account=platform_account,
            )
            for member, influencer, platform_account in rows[:limit]
        )
        return CandidateRunMemberPage(
            items=items,
            next_cursor=items[-1].member.id if len(rows) > limit and items else None,
        )

    async def get_collection_job(
        self,
        collection_job_id: UUID,
        *,
        department_id: UUID,
    ) -> CollectionJob | None:
        statement = select(CollectionJob).where(
            CollectionJob.id == collection_job_id,
            CollectionJob.department_id == department_id,
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def capture_input_watermark(
        self,
        *,
        pool: CandidatePool,
        policy: SellerTargetingPolicy | BuyerTargetingPolicy,
    ) -> dict[str, object]:
        current_contacts = (
            select(
                InfluencerContact.influencer_id.label("influencer_id"),
                func.max(InfluencerContact.updated_at).label("updated_at"),
            )
            .where(InfluencerContact.is_current.is_(True))
            .group_by(InfluencerContact.influencer_id)
            .subquery("targeting_current_contacts")
        )
        statement = select(
            func.count(InfluencerPlatformAccount.id),
            func.max(InfluencerPlatformAccount.updated_at),
            func.max(InfluencerCurrentMetrics.updated_at),
            func.max(InfluencerSourceState.updated_at),
            func.max(current_contacts.c.updated_at),
        ).select_from(InfluencerPlatformAccount)
        statement = (
            statement.join(Influencer, Influencer.id == InfluencerPlatformAccount.influencer_id)
            .outerjoin(
                InfluencerCurrentMetrics,
                and_(
                    InfluencerCurrentMetrics.platform_account_id == InfluencerPlatformAccount.id,
                    InfluencerCurrentMetrics.source == InfluencerPlatformAccount.source,
                ),
            )
            .outerjoin(
                InfluencerSourceState,
                and_(
                    InfluencerSourceState.platform_account_id == InfluencerPlatformAccount.id,
                    InfluencerSourceState.source == InfluencerPlatformAccount.source,
                ),
            )
            .outerjoin(current_contacts, current_contacts.c.influencer_id == Influencer.id)
            .where(
                InfluencerPlatformAccount.is_active.is_(True),
                *visible_influencer_criteria(),
            )
        )
        if pool.kind is CandidatePoolKind.POTENTIAL_BUYER:
            assert pool.source_collection_job_id is not None
            statement = statement.where(
                _committed_provenance_predicate(
                    cast(ColumnElement[UUID], InfluencerPlatformAccount.id),
                    pool.source_collection_job_id,
                )
            )
        count, account_updated, metrics_updated, source_updated, contacts_updated = (
            await self.session.execute(statement)
        ).one()
        watermark: dict[str, object] = {
            "schema_version": 1,
            "candidate_count": int(count or 0),
            "max_platform_account_updated_at": _utc(account_updated),
            "max_current_metrics_updated_at": _utc(metrics_updated),
            "max_source_state_updated_at": _utc(source_updated),
            "max_current_contact_updated_at": _utc(contacts_updated),
            "policy_version": None,
            "policy_hash": policy.canonical_hash,
        }
        if pool.source_collection_job_id is not None:
            collection = await self.get_collection_job(
                pool.source_collection_job_id,
                department_id=pool.department_id,
            )
            if collection is not None:
                snapshot = CollectionContextSnapshot(
                    source_collection_job_id=collection.id,
                    industry=collection.industry,
                    subdirection=collection.subdirection,
                    updated_at=_utc(collection.updated_at),
                )
                watermark["source_collection_job_id"] = collection.id
                watermark["collection_context"] = snapshot.model_dump(mode="json")
        return watermark

    @staticmethod
    def collection_context_snapshot(
        *,
        pool: CandidatePool,
        input_watermark: dict[str, object] | None,
    ) -> CollectionContextSnapshot | None:
        """Read reservation-time Buyer context without consulting live CollectionJob state."""

        if pool.kind is not CandidatePoolKind.POTENTIAL_BUYER:
            return None
        if pool.source_collection_job_id is None or input_watermark is None:
            return None
        raw_snapshot = input_watermark.get("collection_context")
        if not isinstance(raw_snapshot, dict):
            return None
        try:
            snapshot = CollectionContextSnapshot.model_validate(raw_snapshot)
        except ValueError:
            return None
        if snapshot.source_collection_job_id != pool.source_collection_job_id:
            return None
        return snapshot

    async def iter_candidate_fact_batches(
        self,
        *,
        pool: CandidatePool,
        policy: SellerTargetingPolicy | BuyerTargetingPolicy,
        as_of: datetime,
        freshness_policy: FreshnessPolicy,
        collection_context: CollectionContextSnapshot | None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> AsyncIterator[tuple[CandidateFactBundle, ...]]:
        """Stream canonical candidate facts in bounded, fixed-query batches."""

        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
        cursor: UUID | None = None
        if pool.kind is CandidatePoolKind.POTENTIAL_BUYER:
            if pool.source_collection_job_id is None:
                raise ValueError("Buyer pools require source_collection_job_id")

        while True:
            accounts = await self._account_batch(
                pool=pool,
                cursor=cursor,
                batch_size=batch_size,
            )
            if not accounts:
                return
            cursor = accounts[-1].id
            facts = await self._hydrate_fact_batch(
                accounts=accounts,
                collection_context=collection_context,
                source_collection_job_id=pool.source_collection_job_id,
                buyer=pool.kind is CandidatePoolKind.POTENTIAL_BUYER,
                as_of=as_of,
                freshness_policy=freshness_policy,
            )
            yield facts

    async def _account_batch(
        self,
        *,
        pool: CandidatePool,
        cursor: UUID | None,
        batch_size: int,
    ) -> tuple[InfluencerPlatformAccount, ...]:
        statement = (
            select(InfluencerPlatformAccount)
            .join(Influencer, Influencer.id == InfluencerPlatformAccount.influencer_id)
            .where(
                InfluencerPlatformAccount.is_active.is_(True),
                *visible_influencer_criteria(),
            )
        )
        if cursor is not None:
            statement = statement.where(InfluencerPlatformAccount.id > cursor)
        if pool.kind is CandidatePoolKind.POTENTIAL_BUYER:
            if pool.source_collection_job_id is None:
                return ()
            statement = statement.where(
                _committed_provenance_predicate(
                    cast(ColumnElement[UUID], InfluencerPlatformAccount.id),
                    pool.source_collection_job_id,
                )
            )
        statement = statement.order_by(InfluencerPlatformAccount.id).limit(batch_size)
        return tuple((await self.session.execute(statement)).scalars())

    async def _hydrate_fact_batch(
        self,
        *,
        accounts: tuple[InfluencerPlatformAccount, ...],
        collection_context: CollectionContextSnapshot | None,
        source_collection_job_id: UUID | None,
        buyer: bool,
        as_of: datetime,
        freshness_policy: FreshnessPolicy,
    ) -> tuple[CandidateFactBundle, ...]:
        account_ids = tuple(account.id for account in accounts)
        influencer_ids = tuple({account.influencer_id for account in accounts})
        metrics_rows = tuple(
            (
                await self.session.execute(
                    select(InfluencerCurrentMetrics).where(
                        InfluencerCurrentMetrics.platform_account_id.in_(account_ids)
                    )
                )
            ).scalars()
        )
        contacts = tuple(
            (
                await self.session.execute(
                    select(InfluencerContact.influencer_id, InfluencerContact.type).where(
                        InfluencerContact.influencer_id.in_(influencer_ids),
                        InfluencerContact.is_current.is_(True),
                    )
                )
            ).all()
        )
        state_rows = tuple(
            (
                await self.session.execute(
                    select(InfluencerSourceState, ImportRow)
                    .outerjoin(
                        ImportRow,
                        and_(
                            ImportRow.id == InfluencerSourceState.last_import_row_id,
                            ImportRow.import_job_id == InfluencerSourceState.last_import_job_id,
                        ),
                    )
                    .where(InfluencerSourceState.platform_account_id.in_(account_ids))
                )
            )
            .tuples()
            .all()
        )
        freshness = await self._freshness_by_account(account_ids)
        provenance = (
            await self._buyer_provenance(account_ids, source_collection_job_id) if buyer else {}
        )

        account_sources = {account.id: account.source for account in accounts}
        metrics_by_account = {
            row.platform_account_id: row
            for row in metrics_rows
            if account_sources.get(row.platform_account_id) == row.source
        }
        contacts_by_influencer: dict[UUID, set[ContactType]] = defaultdict(set)
        for influencer_id, contact_type in contacts:
            contacts_by_influencer[influencer_id].add(contact_type)
        state_by_account = {
            state.platform_account_id: (state, import_row)
            for state, import_row in state_rows
            if account_sources.get(state.platform_account_id) == state.source
        }

        result: list[CandidateFactBundle] = []
        for account in accounts:
            metric = metrics_by_account.get(account.id)
            state, state_import_row = state_by_account.get(account.id, (None, None))
            metric_values = metric.metrics if metric is not None else {}
            creator_tags, creator_import_job_ids = self._creator_classification(
                account,
                state,
                state_import_row,
            )
            last_observed = freshness.get(account.id)
            freshness_status = (
                freshness_policy.evaluate(last_observed, as_of).status
                if account.id in freshness
                else None
            )
            result.append(
                CandidateFactBundle(
                    influencer_id=account.influencer_id,
                    platform_account_id=account.id,
                    platform=account.platform,
                    source=account.source,
                    current_contact_types=tuple(contacts_by_influencer[account.influencer_id]),
                    followers_count=self._strict_metric(metric_values.get("followers_count")),
                    notes_7d=self._strict_metric(metric_values.get("notes_7d")),
                    notes_60d=self._strict_metric(metric_values.get("notes_60d")),
                    source_tags=account.source_tags,
                    freshness_status=freshness_status,
                    freshness_observed_at=last_observed,
                    source_updated_at=_utc(state.source_updated_at) if state is not None else None,
                    source_collection_job_id=source_collection_job_id,
                    collection_industry=(
                        collection_context.industry if collection_context else None
                    ),
                    collection_subdirection=(
                        collection_context.subdirection if collection_context else None
                    ),
                    creator_classification_tags=creator_tags,
                    source_collection_import_job_ids=tuple(provenance.get(account.id, ())),
                    creator_classification_import_job_ids=creator_import_job_ids,
                )
            )
        return tuple(result)

    @staticmethod
    def _strict_metric(value: object) -> int | None:
        return value if type(value) is int and value >= 0 else None

    @staticmethod
    def _creator_classification(
        account: InfluencerPlatformAccount,
        state: InfluencerSourceState | None,
        state_import_row: ImportRow | None,
    ) -> tuple[tuple[str, ...] | None, tuple[UUID, ...]]:
        """Return XHS tags only when the exact tag snapshot has import provenance.

        Source-state fields are merged by Import and may retain tags from an
        older row after a newer tagless update.  ``last_import_job_id`` alone
        therefore cannot prove which import supplied the classification.
        """

        if account.platform is not Platform.XIAOHONGSHU:
            return (), ()
        if state is None or state_import_row is None:
            return None, ()
        if (
            state_import_row.import_job_id != state.last_import_job_id
            or state_import_row.id != state.last_import_row_id
            or state_import_row.committed_at is None
            or state_import_row.committed_action
            not in {
                ImportRowAction.CREATE,
                ImportRowAction.UPDATE,
                ImportRowAction.NO_CHANGE,
            }
        ):
            return None, ()

        state_tags = CandidatePoolRepository._tag_values(
            state.source_data.get("creator_tags") if isinstance(state.source_data, dict) else None
        )
        normalized_data = state_import_row.normalized_data
        public_profile = (
            normalized_data.get("public_profile") if isinstance(normalized_data, dict) else None
        )
        import_tags = CandidatePoolRepository._tag_values(
            public_profile.get("creator_tags") if isinstance(public_profile, dict) else None
        )
        account_tags = (
            CandidatePoolRepository._tag_values(account.source_tags)
            if account.source_tags is not None
            else state_tags
        )
        if (
            state_tags is None
            or import_tags is None
            or account_tags is None
            or state_tags != import_tags
            or account_tags != state_tags
        ):
            return None, ()
        return state_tags, (state.last_import_job_id,) if state_tags else ()

    @staticmethod
    def _tag_values(value: object) -> tuple[str, ...] | None:
        if not isinstance(value, list):
            return None
        if any(not isinstance(item, str) or not item.strip() for item in value):
            return None
        return tuple(sorted({item.strip() for item in value}))

    async def _freshness_by_account(
        self, account_ids: tuple[UUID, ...]
    ) -> dict[UUID, datetime | None]:
        lineage = eligible_huitun_freshness()
        statement = select(lineage.c.platform_account_id, lineage.c.last_observed_at).where(
            lineage.c.platform_account_id.in_(account_ids)
        )
        return {
            account_id: _utc(last_observed_at)
            for account_id, last_observed_at in (await self.session.execute(statement)).all()
        }

    async def _buyer_provenance(
        self,
        account_ids: tuple[UUID, ...],
        source_collection_job_id: UUID | None,
    ) -> dict[UUID, tuple[UUID, ...]]:
        if source_collection_job_id is None:
            return {}
        committed_actions = (
            ImportRowAction.CREATE,
            ImportRowAction.UPDATE,
            ImportRowAction.NO_CHANGE,
        )
        statement = (
            select(
                ImportRow.matched_platform_account_id,
                ImportJob.id,
            )
            .join(ImportJob, ImportJob.id == ImportRow.import_job_id)
            .join(
                ImportJobFile,
                and_(
                    ImportJobFile.id == ImportRow.import_job_file_id,
                    ImportJobFile.import_job_id == ImportRow.import_job_id,
                ),
            )
            .where(
                ImportRow.matched_platform_account_id.in_(account_ids),
                ImportRow.committed_at.is_not(None),
                ImportRow.committed_action.in_(committed_actions),
                ImportJob.collection_job_id == source_collection_job_id,
                ImportJob.status == ImportJobStatus.COMPLETED,
                ImportJob.confirmed_revision.is_not(None),
                ImportRow.preview_revision == ImportJob.confirmed_revision,
                ImportJobFile.status == ImportJobFileStatus.READY,
            )
        )
        values: dict[UUID, set[UUID]] = defaultdict(set)
        for account_id, import_job_id in (await self.session.execute(statement)).all():
            if account_id is not None:
                values[account_id].add(import_job_id)
        return {account_id: tuple(sorted(job_ids)) for account_id, job_ids in values.items()}

    def add_evaluation_batch(
        self,
        *,
        run: CandidatePoolRun,
        evaluations: Iterable[tuple[CandidateFactBundle, TargetingEvaluation]],
    ) -> tuple[int, int, int]:
        """Stage only MATCH/UNKNOWN rows; the caller owns the surrounding transaction."""

        match_count = 0
        unknown_count = 0
        not_match_count = 0
        members: list[CandidatePoolMember] = []
        for facts, evaluation in evaluations:
            if evaluation.result.value == CandidateResult.MATCH.value:
                match_count += 1
                result = CandidateResult.MATCH
            elif evaluation.result.value == CandidateResult.UNKNOWN.value:
                unknown_count += 1
                result = CandidateResult.UNKNOWN
            else:
                not_match_count += 1
                continue
            members.append(
                CandidatePoolMember(
                    run_id=run.id,
                    influencer_id=facts.influencer_id,
                    platform_account_id=facts.platform_account_id,
                    result=result,
                    reason_codes=[reason_code.value for reason_code in evaluation.reason_codes],
                    redacted_evidence=evaluation.redacted_evidence,
                    evidence_hash=evaluation.evidence_hash,
                )
            )
        self.session.add_all(members)
        return match_count, unknown_count, not_match_count


__all__ = [
    "CandidatePoolPage",
    "CandidatePoolRepository",
    "CandidateRunMemberPage",
    "CandidateRunPage",
    "DEFAULT_BATCH_SIZE",
    "MAX_BATCH_SIZE",
]
