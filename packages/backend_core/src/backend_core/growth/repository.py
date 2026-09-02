"""Set-based persistence and canonical fact assembly for deterministic targeting."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend_core.auth.models import Operator
from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityProvider,
    ContentActivityResult,
    ContentActivitySemantics,
    ProviderAccountIdentityNamespace,
    ProviderAccountIdentityVerificationState,
)
from backend_core.content_activity.huitun_douyin import (
    HUITUN_DOUYIN_IDENTITY_CONTRACT_VERSION,
    HUITUN_DOUYIN_IDENTITY_SOURCE,
)
from backend_core.content_activity.models import ContentActivityObservation, ProviderAccountIdentity
from backend_core.growth.enums import (
    BuyerLeadTier,
    CandidatePoolKind,
    CandidatePoolStatus,
    CandidateResult,
    Phase3AOperationScope,
)
from backend_core.growth.models import (
    CandidatePool,
    CandidatePoolMember,
    CandidatePoolRun,
    Phase3AIdempotencyRecord,
    TargetingPolicy,
)
from backend_core.growth.targeting import (
    BuyerLeadTierDecision,
    BuyerProspectRuleTargetingPolicy,
    BuyerTargetingPolicy,
    CandidateFactBundle,
    CollectionContextSnapshot,
    ContentActivityFact,
    GreyDolphinActivityFact,
    SellerTargetingPolicy,
    TargetingEvaluation,
)
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportRowAction,
    ImportSourceType,
)
from backend_core.imports.models import CollectionJob, ImportJob, ImportJobFile, ImportRow
from backend_core.influencers.enums import ContactType, DataSource, Platform
from backend_core.influencers.freshness import FreshnessPolicy
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
)
from backend_core.outreach.enums import OutreachEventType
from backend_core.outreach.models import OutreachEvent
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
class BuyerProspectRuleRecord:
    """One closed Market Prospect Rule with its current immutable policy."""

    pool: CandidatePool
    owner: Operator
    policy: TargetingPolicy
    latest_run: CandidatePoolRun | None


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
    collection_job_id: UUID | ColumnElement[UUID],
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


def _committed_provenance_as_of_predicate(
    account_id: ColumnElement[UUID],
    collection_job_id: UUID,
    as_of: datetime,
) -> ColumnElement[bool]:
    """Bound Buyer Prospect source membership to the durable Run clock."""

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
                ImportRow.committed_at <= as_of,
                ImportRow.committed_action.in_(committed_actions),
                ImportJob.collection_job_id == collection_job_id,
                ImportJob.status == ImportJobStatus.COMPLETED,
                ImportJob.confirmed_revision.is_not(None),
                ImportRow.preview_revision == ImportJob.confirmed_revision,
                ImportJobFile.status == ImportJobFileStatus.READY,
            )
        ),
    )


def _source_collection_job_id_for_policy(
    pool: CandidatePool,
    policy: SellerTargetingPolicy | BuyerTargetingPolicy | BuyerProspectRuleTargetingPolicy,
) -> UUID | None:
    """Keep a historical Market run bound to its immutable policy source."""

    if isinstance(policy, BuyerProspectRuleTargetingPolicy):
        return policy.source_collection_job_id
    return pool.source_collection_job_id


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

    async def get_buyer_prospect_rule(
        self,
        *,
        pool_id: UUID,
        department_id: UUID,
        for_update: bool = False,
    ) -> BuyerProspectRuleRecord | None:
        statement = (
            select(CandidatePool, Operator, TargetingPolicy)
            .join(
                Operator,
                and_(
                    Operator.id == CandidatePool.owner_operator_id,
                    Operator.department_id == CandidatePool.department_id,
                ),
            )
            .join(
                TargetingPolicy,
                and_(
                    TargetingPolicy.id == CandidatePool.current_policy_id,
                    TargetingPolicy.pool_id == CandidatePool.id,
                ),
            )
            .where(
                CandidatePool.id == pool_id,
                CandidatePool.department_id == department_id,
                CandidatePool.kind == CandidatePoolKind.POTENTIAL_BUYER,
                TargetingPolicy.definition["policy_type"].as_string()
                == BuyerProspectRuleTargetingPolicy.model_fields["policy_type"].default,
            )
        )
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        row = (await self.session.execute(statement)).first()
        if row is None:
            return None
        pool, owner, policy = row
        latest_run = await self.get_latest_run_for_policy(pool_id=pool.id, policy_id=policy.id)
        return BuyerProspectRuleRecord(
            pool=pool,
            owner=owner,
            policy=policy,
            latest_run=latest_run,
        )

    async def list_buyer_prospect_rules(
        self,
        *,
        department_id: UUID,
        cursor: UUID | None,
        limit: int,
        include_archived: bool,
    ) -> tuple[tuple[BuyerProspectRuleRecord, ...], UUID | None]:
        statement = (
            select(CandidatePool, Operator, TargetingPolicy)
            .join(
                Operator,
                and_(
                    Operator.id == CandidatePool.owner_operator_id,
                    Operator.department_id == CandidatePool.department_id,
                ),
            )
            .join(
                TargetingPolicy,
                and_(
                    TargetingPolicy.id == CandidatePool.current_policy_id,
                    TargetingPolicy.pool_id == CandidatePool.id,
                ),
            )
            .where(
                CandidatePool.department_id == department_id,
                CandidatePool.kind == CandidatePoolKind.POTENTIAL_BUYER,
                TargetingPolicy.definition["policy_type"].as_string()
                == BuyerProspectRuleTargetingPolicy.model_fields["policy_type"].default,
            )
        )
        if not include_archived:
            statement = statement.where(CandidatePool.status != CandidatePoolStatus.ARCHIVED)
        if cursor is not None:
            statement = statement.where(CandidatePool.id > cursor)
        statement = statement.order_by(CandidatePool.id).limit(limit + 1)
        rows = tuple((await self.session.execute(statement)).all())
        visible_rows = rows[:limit]
        latest_runs = await self.latest_runs_for_policy_ids(
            policy_ids=tuple(policy.id for _pool, _owner, policy in visible_rows)
        )
        items = tuple(
            BuyerProspectRuleRecord(
                pool=pool,
                owner=owner,
                policy=policy,
                latest_run=latest_runs.get(policy.id),
            )
            for pool, owner, policy in visible_rows
        )
        return (
            items,
            items[-1].pool.id if len(rows) > limit and items else None,
        )

    async def list_buyer_prospect_source_collection_jobs(
        self,
        *,
        department_id: UUID,
    ) -> tuple[CollectionJob, ...]:
        """Return only same-Department jobs with durable Buyer source evidence.

        Category mapping remains a frozen service concern, but a chooser must
        never advertise a CollectionJob that cannot supply a committed Buyer
        candidate at all.  This mirrors the exact provenance predicate used by
        run materialization without doing one query per job.
        """

        eligible_source = exists(
            select(1)
            .select_from(InfluencerPlatformAccount)
            .join(Influencer, Influencer.id == InfluencerPlatformAccount.influencer_id)
            .where(
                InfluencerPlatformAccount.is_active.is_(True),
                *visible_influencer_criteria(),
                _committed_provenance_predicate(
                    cast(ColumnElement[UUID], InfluencerPlatformAccount.id),
                    cast(ColumnElement[UUID], CollectionJob.id),
                ),
            )
        )
        jobs = await self.session.scalars(
            select(CollectionJob)
            .where(
                CollectionJob.department_id == department_id,
                CollectionJob.status.in_(
                    (CollectionJobStatus.ACTIVE, CollectionJobStatus.COMPLETED)
                ),
                eligible_source,
            )
            .order_by(CollectionJob.name, CollectionJob.id)
        )
        return tuple(jobs)

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
        buyer_lead_tier: BuyerLeadTier | None = None,
    ) -> CandidateRunMemberPage:
        # Seller retains its historical MATCH/UNKNOWN member contract. Buyer
        # Lead Tier runs materialize NOT_MATCH too so SAME/RELATED never vanish.
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
                or_(
                    CandidatePool.kind == CandidatePoolKind.POTENTIAL_BUYER,
                    CandidatePoolMember.result.in_(
                        (CandidateResult.MATCH, CandidateResult.UNKNOWN)
                    ),
                ),
            )
        )
        if result is not None:
            statement = statement.where(CandidatePoolMember.result == result)
        if buyer_lead_tier is not None:
            statement = statement.where(CandidatePoolMember.buyer_lead_tier == buyer_lead_tier)
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
        for_update: bool = False,
    ) -> CollectionJob | None:
        statement = select(CollectionJob).where(
            CollectionJob.id == collection_job_id,
            CollectionJob.department_id == department_id,
        )
        if for_update:
            # The CollectionJob is the natural bootstrap concurrency boundary:
            # only one semantically identical Buyer Pool may be created from it.
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def has_committed_buyer_source_candidates(
        self,
        *,
        collection_job_id: UUID,
    ) -> bool:
        """Require the same exact source provenance used by Buyer materialization."""

        statement = select(
            exists(
                select(1)
                .select_from(InfluencerPlatformAccount)
                .join(Influencer, Influencer.id == InfluencerPlatformAccount.influencer_id)
                .where(
                    InfluencerPlatformAccount.is_active.is_(True),
                    *visible_influencer_criteria(),
                    _committed_provenance_predicate(
                        cast(ColumnElement[UUID], InfluencerPlatformAccount.id),
                        collection_job_id,
                    ),
                )
            )
        )
        return bool((await self.session.execute(statement)).scalar())

    async def get_active_buyer_pool_for_bootstrap(
        self,
        *,
        department_id: UUID,
        collection_job_id: UUID,
        policy_hash: str,
    ) -> CandidatePool | None:
        """Find a semantically identical current Buyer Pool under the Job lock."""

        statement = (
            select(CandidatePool)
            .join(TargetingPolicy, TargetingPolicy.id == CandidatePool.current_policy_id)
            .where(
                CandidatePool.department_id == department_id,
                CandidatePool.kind == CandidatePoolKind.POTENTIAL_BUYER,
                CandidatePool.source_collection_job_id == collection_job_id,
                CandidatePool.status == CandidatePoolStatus.ACTIVE,
                TargetingPolicy.canonical_hash == policy_hash,
            )
            .order_by(CandidatePool.created_at, CandidatePool.id)
            .limit(1)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_first_run_for_policy(
        self,
        *,
        pool_id: UUID,
        policy_id: UUID,
    ) -> CandidatePoolRun | None:
        statement = (
            select(CandidatePoolRun)
            .where(
                CandidatePoolRun.pool_id == pool_id,
                CandidatePoolRun.policy_id == policy_id,
            )
            .order_by(CandidatePoolRun.created_at, CandidatePoolRun.id)
            .limit(1)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_latest_run_for_policy(
        self,
        *,
        pool_id: UUID,
        policy_id: UUID,
    ) -> CandidatePoolRun | None:
        return cast(
            CandidatePoolRun | None,
            await self.session.scalar(
                select(CandidatePoolRun)
                .where(
                    CandidatePoolRun.pool_id == pool_id,
                    CandidatePoolRun.policy_id == policy_id,
                )
                .order_by(CandidatePoolRun.created_at.desc(), CandidatePoolRun.id.desc())
                .limit(1)
            ),
        )

    async def latest_runs_for_policy_ids(
        self,
        *,
        policy_ids: tuple[UUID, ...],
    ) -> dict[UUID, CandidatePoolRun]:
        if not policy_ids:
            return {}
        ranked = (
            select(
                CandidatePoolRun.id.label("run_id"),
                CandidatePoolRun.policy_id.label("policy_id"),
                func.row_number()
                .over(
                    partition_by=CandidatePoolRun.policy_id,
                    order_by=(CandidatePoolRun.created_at.desc(), CandidatePoolRun.id.desc()),
                )
                .label("row_rank"),
            )
            .where(CandidatePoolRun.policy_id.in_(policy_ids))
            .subquery()
        )
        runs = tuple(
            await self.session.scalars(
                select(CandidatePoolRun)
                .join(ranked, CandidatePoolRun.id == ranked.c.run_id)
                .where(ranked.c.row_rank == 1)
            )
        )
        return {run.policy_id: run for run in runs}

    async def capture_input_watermark(
        self,
        *,
        pool: CandidatePool,
        policy: SellerTargetingPolicy | BuyerTargetingPolicy | BuyerProspectRuleTargetingPolicy,
    ) -> dict[str, object]:
        source_collection_job_id = _source_collection_job_id_for_policy(pool, policy)
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
            assert source_collection_job_id is not None
            statement = statement.where(
                _committed_provenance_predicate(
                    cast(ColumnElement[UUID], InfluencerPlatformAccount.id),
                    source_collection_job_id,
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
        if source_collection_job_id is not None:
            collection = await self.get_collection_job(
                source_collection_job_id,
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
        policy: SellerTargetingPolicy | BuyerTargetingPolicy | BuyerProspectRuleTargetingPolicy,
        input_watermark: dict[str, object] | None,
    ) -> CollectionContextSnapshot | None:
        """Read reservation-time Buyer context without consulting live CollectionJob state."""

        if pool.kind is not CandidatePoolKind.POTENTIAL_BUYER:
            return None
        source_collection_job_id = _source_collection_job_id_for_policy(pool, policy)
        if source_collection_job_id is None or input_watermark is None:
            return None
        raw_snapshot = input_watermark.get("collection_context")
        if not isinstance(raw_snapshot, dict):
            return None
        try:
            snapshot = CollectionContextSnapshot.model_validate(raw_snapshot)
        except ValueError:
            return None
        if snapshot.source_collection_job_id != source_collection_job_id:
            return None
        return snapshot

    async def iter_candidate_fact_batches(
        self,
        *,
        pool: CandidatePool,
        policy: SellerTargetingPolicy | BuyerTargetingPolicy | BuyerProspectRuleTargetingPolicy,
        as_of: datetime,
        freshness_policy: FreshnessPolicy,
        collection_context: CollectionContextSnapshot | None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> AsyncIterator[tuple[CandidateFactBundle, ...]]:
        """Stream canonical candidate facts in bounded, fixed-query batches."""

        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
        cursor: UUID | None = None
        source_collection_job_id = _source_collection_job_id_for_policy(pool, policy)
        if pool.kind is CandidatePoolKind.POTENTIAL_BUYER:
            if source_collection_job_id is None:
                raise ValueError("Buyer pools require source_collection_job_id")

        while True:
            accounts = await self._account_batch(
                pool=pool,
                cursor=cursor,
                batch_size=batch_size,
                as_of=as_of,
                market_prospect_rule=isinstance(policy, BuyerProspectRuleTargetingPolicy),
                source_collection_job_id=source_collection_job_id,
            )
            if not accounts:
                return
            cursor = accounts[-1].id
            facts = await self._hydrate_fact_batch(
                accounts=accounts,
                collection_context=collection_context,
                source_collection_job_id=source_collection_job_id,
                buyer=pool.kind is CandidatePoolKind.POTENTIAL_BUYER,
                market_prospect_rule=isinstance(policy, BuyerProspectRuleTargetingPolicy),
                department_id=pool.department_id,
                as_of=as_of,
                freshness_policy=freshness_policy,
                include_content_activity=(
                    isinstance(policy, SellerTargetingPolicy)
                    and (policy.content_activity is not None or policy.long_inactivity is not None)
                ),
            )
            yield facts

    async def _account_batch(
        self,
        *,
        pool: CandidatePool,
        cursor: UUID | None,
        batch_size: int,
        as_of: datetime,
        market_prospect_rule: bool,
        source_collection_job_id: UUID | None,
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
            if source_collection_job_id is None:
                return ()
            statement = statement.where(
                (
                    _committed_provenance_as_of_predicate(
                        cast(ColumnElement[UUID], InfluencerPlatformAccount.id),
                        source_collection_job_id,
                        as_of,
                    )
                    if market_prospect_rule
                    else _committed_provenance_predicate(
                        cast(ColumnElement[UUID], InfluencerPlatformAccount.id),
                        source_collection_job_id,
                    )
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
        market_prospect_rule: bool,
        department_id: UUID,
        as_of: datetime,
        freshness_policy: FreshnessPolicy,
        include_content_activity: bool = False,
    ) -> tuple[CandidateFactBundle, ...]:
        account_ids = tuple(account.id for account in accounts)
        influencer_ids = tuple({account.influencer_id for account in accounts})
        metrics_rows = tuple(
            (
                await self.session.execute(
                    select(InfluencerCurrentMetrics, ImportRow, ImportJob, ImportJobFile)
                    .outerjoin(
                        ImportRow,
                        and_(
                            ImportRow.id == InfluencerCurrentMetrics.last_import_row_id,
                            ImportRow.import_job_id == InfluencerCurrentMetrics.last_import_job_id,
                        ),
                    )
                    .outerjoin(ImportJob, ImportJob.id == ImportRow.import_job_id)
                    .outerjoin(
                        ImportJobFile,
                        and_(
                            ImportJobFile.id == ImportRow.import_job_file_id,
                            ImportJobFile.import_job_id == ImportRow.import_job_id,
                        ),
                    )
                    .where(InfluencerCurrentMetrics.platform_account_id.in_(account_ids))
                )
            ).all()
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
                    select(InfluencerSourceState, ImportRow, ImportJob, ImportJobFile)
                    .outerjoin(
                        ImportRow,
                        and_(
                            ImportRow.id == InfluencerSourceState.last_import_row_id,
                            ImportRow.import_job_id == InfluencerSourceState.last_import_job_id,
                        ),
                    )
                    .outerjoin(ImportJob, ImportJob.id == ImportRow.import_job_id)
                    .outerjoin(
                        ImportJobFile,
                        and_(
                            ImportJobFile.id == ImportRow.import_job_file_id,
                            ImportJobFile.import_job_id == ImportRow.import_job_id,
                        ),
                    )
                    .where(InfluencerSourceState.platform_account_id.in_(account_ids))
                )
            )
            .tuples()
            .all()
        )
        freshness = await self._freshness_by_account(account_ids)
        content_activity_by_account = (
            await self._content_activity_facts_as_of(account_ids=account_ids, as_of=as_of)
            if include_content_activity
            else {}
        )
        provenance = (
            await self._buyer_provenance(account_ids, source_collection_job_id) if buyer else {}
        )
        metric_snapshots = (
            await self._latest_metric_snapshots_as_of(account_ids=account_ids, as_of=as_of)
            if market_prospect_rule
            else {}
        )
        buyer_source_provenance = (
            await self._buyer_source_provenance_as_of(
                account_ids=account_ids,
                source_collection_job_id=source_collection_job_id,
                as_of=as_of,
            )
            if market_prospect_rule
            else {}
        )
        owner_by_influencer = (
            {
                influencer_id: owner_operator_id
                for influencer_id, owner_operator_id in (
                    await self.session.execute(
                        select(Influencer.id, Influencer.owner_operator_id).where(
                            Influencer.id.in_(influencer_ids)
                        )
                    )
                ).all()
            }
            if market_prospect_rule
            else {}
        )
        contacted_by_influencer = (
            await self._contacted_outreach_events_as_of(
                influencer_ids=influencer_ids,
                department_id=department_id,
                as_of=as_of,
            )
            if market_prospect_rule
            else {}
        )

        account_sources = {account.id: account.source for account in accounts}
        metrics_by_account = {
            metric.platform_account_id: (metric, import_row, import_job, import_file)
            for metric, import_row, import_job, import_file in metrics_rows
            if account_sources.get(metric.platform_account_id) == metric.source
        }
        contacts_by_influencer: dict[UUID, set[ContactType]] = defaultdict(set)
        for influencer_id, contact_type in contacts:
            contacts_by_influencer[influencer_id].add(contact_type)
        state_by_account = {
            state.platform_account_id: (state, import_row, import_job, import_file)
            for state, import_row, import_job, import_file in state_rows
            if account_sources.get(state.platform_account_id) == state.source
        }

        result: list[CandidateFactBundle] = []
        for account in accounts:
            metric_context = metrics_by_account.get(account.id)
            metric = metric_context[0] if metric_context is not None else None
            metric_import_row = metric_context[1] if metric_context is not None else None
            metric_import_job = metric_context[2] if metric_context is not None else None
            metric_import_file = metric_context[3] if metric_context is not None else None
            state, state_import_row, state_import_job, state_import_file = state_by_account.get(
                account.id, (None, None, None, None)
            )
            metric_values = metric.metrics if metric is not None else {}
            metric_snapshot = metric_snapshots.get(account.id)
            snapshot_values = metric_snapshot.metrics if metric_snapshot is not None else {}
            source_provenance = buyer_source_provenance.get(account.id)
            contacted_event = contacted_by_influencer.get(account.influencer_id)
            creator_tags, creator_import_job_ids = self._creator_classification(
                account,
                state,
                state_import_row,
                state_import_job,
                state_import_file,
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
                    followers_count=(
                        self._strict_metric(snapshot_values.get("followers_count"))
                        if market_prospect_rule
                        else self._strict_metric(metric_values.get("followers_count"))
                    ),
                    follower_metric_snapshot_id=(
                        metric_snapshot.id if metric_snapshot is not None else None
                    ),
                    follower_metric_snapshot_hash=(
                        metric_snapshot.metrics_hash if metric_snapshot is not None else None
                    ),
                    follower_metric_snapshot_import_job_id=(
                        metric_snapshot.import_job_id if metric_snapshot is not None else None
                    ),
                    follower_metric_snapshot_import_row_id=(
                        metric_snapshot.import_row_id if metric_snapshot is not None else None
                    ),
                    follower_metric_snapshot_captured_at=(
                        _utc(metric_snapshot.captured_at) if metric_snapshot is not None else None
                    ),
                    notes_7d=self._strict_metric(metric_values.get("notes_7d")),
                    notes_60d=self._strict_metric(metric_values.get("notes_60d")),
                    source_tags=account.source_tags,
                    freshness_status=freshness_status,
                    freshness_observed_at=last_observed,
                    source_updated_at=_utc(state.source_updated_at) if state is not None else None,
                    content_activity=content_activity_by_account.get(account.id),
                    grey_dolphin_activity=self._grey_dolphin_activity_fact(
                        account=account,
                        metric=metric,
                        import_row=metric_import_row,
                        import_job=metric_import_job,
                        import_file=metric_import_file,
                    ),
                    source_collection_job_id=source_collection_job_id,
                    buyer_source_import_job_file_id=(
                        source_provenance[0] if source_provenance is not None else None
                    ),
                    buyer_source_import_job_id=(
                        source_provenance[1] if source_provenance is not None else None
                    ),
                    buyer_source_import_row_id=(
                        source_provenance[2] if source_provenance is not None else None
                    ),
                    buyer_source_acquired_at=(
                        source_provenance[3] if source_provenance is not None else None
                    ),
                    collection_industry=(
                        collection_context.industry if collection_context else None
                    ),
                    collection_subdirection=(
                        collection_context.subdirection if collection_context else None
                    ),
                    creator_classification_tags=creator_tags,
                    source_collection_import_job_ids=tuple(provenance.get(account.id, ())),
                    creator_classification_import_job_ids=creator_import_job_ids,
                    owner_operator_id=owner_by_influencer.get(account.influencer_id),
                    contacted_outreach_event_id=(
                        contacted_event[0] if contacted_event is not None else None
                    ),
                    contacted_outreach_occurred_at=(
                        contacted_event[1] if contacted_event is not None else None
                    ),
                )
            )
        return tuple(result)

    async def _latest_metric_snapshots_as_of(
        self,
        *,
        account_ids: tuple[UUID, ...],
        as_of: datetime,
    ) -> dict[UUID, InfluencerMetricSnapshot]:
        """Resolve the latest immutable metric snapshot visible to one Run."""

        if not account_ids:
            return {}
        ranked = (
            select(
                InfluencerMetricSnapshot.id.label("snapshot_id"),
                func.row_number()
                .over(
                    partition_by=InfluencerMetricSnapshot.platform_account_id,
                    order_by=(
                        InfluencerMetricSnapshot.captured_at.desc(),
                        InfluencerMetricSnapshot.id.desc(),
                    ),
                )
                .label("row_rank"),
            )
            .where(
                InfluencerMetricSnapshot.platform_account_id.in_(account_ids),
                InfluencerMetricSnapshot.captured_at <= as_of,
            )
            .subquery()
        )
        snapshots = tuple(
            await self.session.scalars(
                select(InfluencerMetricSnapshot)
                .join(
                    ranked,
                    InfluencerMetricSnapshot.id == ranked.c.snapshot_id,
                )
                .where(ranked.c.row_rank == 1)
            )
        )
        return {snapshot.platform_account_id: snapshot for snapshot in snapshots}

    async def _buyer_source_provenance_as_of(
        self,
        *,
        account_ids: tuple[UUID, ...],
        source_collection_job_id: UUID | None,
        as_of: datetime,
    ) -> dict[UUID, tuple[UUID, UUID, UUID, datetime | None]]:
        """Return one source-file lineage record per account for Market rules.

        Finite windows rely only on a trusted, known ``source_acquired_at``;
        the ALL choice still retains the committed READY-file provenance even
        when historical acquisition time is unavailable.
        """

        if not account_ids or source_collection_job_id is None:
            return {}
        committed_actions = (
            ImportRowAction.CREATE,
            ImportRowAction.UPDATE,
            ImportRowAction.NO_CHANGE,
        )
        rows = (
            await self.session.execute(
                select(
                    ImportRow.matched_platform_account_id,
                    ImportJobFile.id,
                    ImportJob.id,
                    ImportRow.id,
                    ImportJobFile.source_acquired_at,
                    ImportJobFile.source_acquired_at_confirmation_required,
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
                    ImportRow.committed_at <= as_of,
                    ImportRow.committed_action.in_(committed_actions),
                    ImportJob.collection_job_id == source_collection_job_id,
                    ImportJob.status == ImportJobStatus.COMPLETED,
                    ImportJob.confirmed_revision.is_not(None),
                    ImportRow.preview_revision == ImportJob.confirmed_revision,
                    ImportJobFile.status == ImportJobFileStatus.READY,
                )
            )
        ).all()
        grouped: dict[
            UUID, list[tuple[UUID, UUID, UUID, datetime | None, bool]]
        ] = defaultdict(list)
        for account_id, file_id, job_id, row_id, acquired_at, confirmation_required in rows:
            if account_id is not None:
                grouped[account_id].append(
                    (
                        file_id,
                        job_id,
                        row_id,
                        _utc(acquired_at),
                        bool(confirmation_required),
                    )
                )

        result: dict[UUID, tuple[UUID, UUID, UUID, datetime | None]] = {}
        for account_id, candidates in grouped.items():
            trusted_as_of = [
                item
                for item in candidates
                if item[3] is not None and not item[4] and item[3] <= as_of
            ]
            if trusted_as_of:
                selected = max(trusted_as_of, key=lambda item: (item[3], item[0]))
            else:
                selected = max(
                    candidates,
                    key=lambda item: (
                        item[3] is not None,
                        item[3] or datetime.min.replace(tzinfo=UTC),
                        item[0],
                    ),
                )
            result[account_id] = (
                selected[0],
                selected[1],
                selected[2],
                None if selected[4] else selected[3],
            )
        return result

    async def _contacted_outreach_events_as_of(
        self,
        *,
        influencer_ids: tuple[UUID, ...],
        department_id: UUID,
        as_of: datetime,
    ) -> dict[UUID, tuple[UUID, datetime]]:
        """Resolve the latest same-Department OUTREACH_SENT event at Run time."""

        if not influencer_ids:
            return {}
        events = tuple(
            await self.session.scalars(
                select(OutreachEvent)
                .where(
                    OutreachEvent.influencer_id.in_(influencer_ids),
                    OutreachEvent.department_id == department_id,
                    OutreachEvent.event_type == OutreachEventType.OUTREACH_SENT,
                    OutreachEvent.occurred_at <= as_of,
                )
                .order_by(
                    OutreachEvent.influencer_id,
                    OutreachEvent.occurred_at.desc(),
                    OutreachEvent.id.desc(),
                )
            )
        )
        result: dict[UUID, tuple[UUID, datetime]] = {}
        for event in events:
            result.setdefault(event.influencer_id, (event.id, _utc(event.occurred_at) or as_of))
        return result

    @classmethod
    def _grey_dolphin_activity_fact(
        cls,
        *,
        account: InfluencerPlatformAccount,
        metric: InfluencerCurrentMetrics | None,
        import_row: ImportRow | None,
        import_job: ImportJob | None,
        import_file: ImportJobFile | None,
    ) -> GreyDolphinActivityFact | None:
        """Return only a provenance-bound coherent Huitun aggregate snapshot.

        ``InfluencerCurrentMetrics.metrics`` stores both counters together, so
        the row is the atomic coherence boundary.  Its exact last-import row
        must also be a confirmed manual Huitun export with a real source
        acquisition time; imported-at, account creation source, and a later
        unrelated Huitun observation never make an aggregate usable.
        """

        if (
            account.source is not DataSource.HUITUN
            or metric is None
            or metric.source is not DataSource.HUITUN
            or import_row is None
            or import_job is None
            or import_file is None
            or import_row.id != metric.last_import_row_id
            or import_row.import_job_id != metric.last_import_job_id
            or import_row.committed_at is None
            or import_row.committed_action
            not in {
                ImportRowAction.CREATE,
                ImportRowAction.UPDATE,
                ImportRowAction.NO_CHANGE,
            }
            or import_job.id != metric.last_import_job_id
            or import_job.status is not ImportJobStatus.COMPLETED
            or import_job.confirmed_revision is None
            or import_row.preview_revision != import_job.confirmed_revision
            or import_job.source_type is not ImportSourceType.MANUAL_HUITUN_EXPORT
            or import_file.status is not ImportJobFileStatus.READY
            or import_file.source_acquired_at is None
            or import_file.source_acquired_at_confirmation_required
        ):
            return None
        metrics = metric.metrics if isinstance(metric.metrics, dict) else {}
        return GreyDolphinActivityFact(
            observed_at=_utc(import_file.source_acquired_at),
            source_updated_at=_utc(metric.source_updated_at),
            notes_7d=cls._strict_metric(metrics.get("notes_7d")),
            notes_60d=cls._strict_metric(metrics.get("notes_60d")),
            import_job_id=metric.last_import_job_id,
            import_row_id=metric.last_import_row_id,
        )

    async def _content_activity_facts_as_of(
        self,
        *,
        account_ids: tuple[UUID, ...],
        as_of: datetime,
    ) -> dict[UUID, ContentActivityFact]:
        """Hydrate immutable activity evidence as it stood at one Candidate Run instant.

        The mutable projection is optimized for present-tense library reads, but
        a deferred Candidate Run must not change merely because a provider check
        completes after its already-persisted ``as_of``.  Two fixed, set-based
        window queries reconstruct the latest attempt and latest usable trusted
        current-public observation per account at that instant.  A same-instant
        tie is deliberately recorded as ambiguous rather than arbitrarily
        ordered.  Candidate evaluation then fails closed even when one member
        of the tie happens to have a UUID that sorts first.
        """

        if not account_ids:
            return {}
        as_of_utc = _utc(as_of)
        if as_of_utc is None:  # pragma: no cover - typed non-null guard
            raise ValueError("content activity as_of is required")

        latest_ranked = (
            select(
                ContentActivityObservation.id.label("observation_id"),
                func.row_number()
                .over(
                    partition_by=ContentActivityObservation.platform_account_id,
                    order_by=(
                        ContentActivityObservation.observed_at.desc(),
                        ContentActivityObservation.id.desc(),
                    ),
                )
                .label("row_rank"),
                func.count()
                .over(
                    partition_by=(
                        ContentActivityObservation.platform_account_id,
                        ContentActivityObservation.observed_at,
                    )
                )
                .label("same_instant_attempt_count"),
            )
            .where(
                ContentActivityObservation.platform_account_id.in_(account_ids),
                ContentActivityObservation.observed_at <= as_of_utc,
            )
            .subquery()
        )
        latest_attempt_rows = (
            await self.session.execute(
                select(
                    ContentActivityObservation,
                    latest_ranked.c.same_instant_attempt_count,
                )
                .join(
                    latest_ranked,
                    ContentActivityObservation.id == latest_ranked.c.observation_id,
                )
                .where(latest_ranked.c.row_rank == 1)
            )
        ).all()

        # Huitun is a returned-scope semantic evidence stream, never a
        # trusted-current projection. Keep its latest attempt separate so a
        # newer raw/encrypted/partial/auth failure overrides an older semantic
        # success for the Douyin V1 rule without changing XHS semantics.
        huitun_latest_ranked = (
            select(
                ContentActivityObservation.id.label("observation_id"),
                func.row_number()
                .over(
                    partition_by=ContentActivityObservation.platform_account_id,
                    order_by=(
                        ContentActivityObservation.observed_at.desc(),
                        ContentActivityObservation.id.desc(),
                    ),
                )
                .label("row_rank"),
                func.count()
                .over(
                    partition_by=(
                        ContentActivityObservation.platform_account_id,
                        ContentActivityObservation.observed_at,
                    )
                )
                .label("same_instant_attempt_count"),
            )
            .where(
                ContentActivityObservation.platform_account_id.in_(account_ids),
                ContentActivityObservation.observed_at <= as_of_utc,
                ContentActivityObservation.platform == Platform.DOUYIN,
                ContentActivityObservation.activity_semantics
                == ContentActivitySemantics.HUITUN_RETURNED_SCOPE,
                ContentActivityObservation.activity_source_provider
                == ContentActivityProvider.HUITUN_DOUYIN_AWEME_LIST,
            )
            .subquery()
        )
        huitun_latest_rows = (
            await self.session.execute(
                select(
                    ContentActivityObservation,
                    huitun_latest_ranked.c.same_instant_attempt_count,
                )
                .join(
                    huitun_latest_ranked,
                    ContentActivityObservation.id == huitun_latest_ranked.c.observation_id,
                )
                .where(huitun_latest_ranked.c.row_rank == 1)
            )
        ).all()

        identity_valid_at_as_of = and_(
            ProviderAccountIdentity.verified_at <= as_of_utc,
            or_(
                ProviderAccountIdentity.verification_state
                == ProviderAccountIdentityVerificationState.VERIFIED_CURRENT,
                and_(
                    ProviderAccountIdentity.verification_state
                    == ProviderAccountIdentityVerificationState.SUPERSEDED,
                    ProviderAccountIdentity.superseded_at > as_of_utc,
                ),
                and_(
                    ProviderAccountIdentity.verification_state
                    == ProviderAccountIdentityVerificationState.REVOKED,
                    ProviderAccountIdentity.revoked_at > as_of_utc,
                ),
            ),
        )

        huitun_identity_valid_at_as_of = and_(
            identity_valid_at_as_of,
            ProviderAccountIdentity.platform == Platform.DOUYIN,
            ProviderAccountIdentity.namespace == ProviderAccountIdentityNamespace.DOUYIN_HUITUN_UID,
            ProviderAccountIdentity.identity_source == HUITUN_DOUYIN_IDENTITY_SOURCE,
            ProviderAccountIdentity.resolver_contract_version
            == HUITUN_DOUYIN_IDENTITY_CONTRACT_VERSION,
        )
        huitun_accepted_result = or_(
            and_(
                ContentActivityObservation.coverage_status
                == ContentActivityCoverageStatus.LATEST_BOUND_PROVEN,
                ContentActivityObservation.activity_result
                == ContentActivityResult.PUBLICATION_FOUND,
            ),
            and_(
                ContentActivityObservation.coverage_status
                == ContentActivityCoverageStatus.LOOKBACK_BOUNDED,
                ContentActivityObservation.activity_result
                == ContentActivityResult.AT_LEAST_LOOKBACK_INACTIVE,
            ),
        )
        huitun_accepted_ranked = (
            select(
                ContentActivityObservation.id.label("observation_id"),
                func.row_number()
                .over(
                    partition_by=ContentActivityObservation.platform_account_id,
                    order_by=(
                        ContentActivityObservation.observed_at.desc(),
                        ContentActivityObservation.id.desc(),
                    ),
                )
                .label("row_rank"),
            )
            .join(
                ProviderAccountIdentity,
                and_(
                    ProviderAccountIdentity.id
                    == ContentActivityObservation.provider_account_identity_id,
                    ProviderAccountIdentity.platform_account_id
                    == ContentActivityObservation.platform_account_id,
                    ProviderAccountIdentity.platform == ContentActivityObservation.platform,
                ),
            )
            .where(
                ContentActivityObservation.platform_account_id.in_(account_ids),
                ContentActivityObservation.observed_at <= as_of_utc,
                ContentActivityObservation.platform == Platform.DOUYIN,
                ContentActivityObservation.activity_semantics
                == ContentActivitySemantics.HUITUN_RETURNED_SCOPE,
                ContentActivityObservation.activity_source_provider
                == ContentActivityProvider.HUITUN_DOUYIN_AWEME_LIST,
                ContentActivityObservation.observation_status
                == ContentActivityObservationStatus.COMPLETE,
                huitun_accepted_result,
                ContentActivityObservation.provider_account_identity_verification_id.is_not(None),
                huitun_identity_valid_at_as_of,
            )
            .subquery()
        )
        huitun_accepted_observations = tuple(
            await self.session.scalars(
                select(ContentActivityObservation)
                .join(
                    huitun_accepted_ranked,
                    ContentActivityObservation.id == huitun_accepted_ranked.c.observation_id,
                )
                .where(huitun_accepted_ranked.c.row_rank == 1)
            )
        )
        trusted_ranked = (
            select(
                ContentActivityObservation.id.label("observation_id"),
                func.row_number()
                .over(
                    partition_by=ContentActivityObservation.platform_account_id,
                    order_by=(
                        ContentActivityObservation.observed_at.desc(),
                        ContentActivityObservation.id.desc(),
                    ),
                )
                .label("row_rank"),
                func.count()
                .over(
                    partition_by=(
                        ContentActivityObservation.platform_account_id,
                        ContentActivityObservation.observed_at,
                    )
                )
                .label("same_instant_count"),
            )
            .join(
                ProviderAccountIdentity,
                and_(
                    ProviderAccountIdentity.id
                    == ContentActivityObservation.provider_account_identity_id,
                    ProviderAccountIdentity.platform_account_id
                    == ContentActivityObservation.platform_account_id,
                    ProviderAccountIdentity.platform == ContentActivityObservation.platform,
                ),
            )
            .where(
                ContentActivityObservation.platform_account_id.in_(account_ids),
                ContentActivityObservation.observed_at <= as_of_utc,
                ContentActivityObservation.observation_status
                == ContentActivityObservationStatus.COMPLETE,
                ContentActivityObservation.coverage_status
                == ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
                ContentActivityObservation.activity_result.in_(
                    (
                        ContentActivityResult.PUBLICATION_FOUND,
                        ContentActivityResult.NO_PUBLIC_CONTENT,
                    )
                ),
                identity_valid_at_as_of,
            )
            .subquery()
        )
        trusted_observations = tuple(
            await self.session.scalars(
                select(ContentActivityObservation)
                .join(
                    trusted_ranked,
                    ContentActivityObservation.id == trusted_ranked.c.observation_id,
                )
                .where(
                    trusted_ranked.c.row_rank == 1,
                    trusted_ranked.c.same_instant_count == 1,
                )
            )
        )

        latest_by_account = {
            observation.platform_account_id: observation
            for observation, _same_instant_attempt_count in latest_attempt_rows
        }
        latest_same_instant_attempt_count_by_account = {
            observation.platform_account_id: int(same_instant_attempt_count)
            for observation, same_instant_attempt_count in latest_attempt_rows
        }
        trusted_by_account = {
            observation.platform_account_id: observation for observation in trusted_observations
        }
        huitun_latest_by_account = {
            observation.platform_account_id: observation
            for observation, _same_instant_attempt_count in huitun_latest_rows
        }
        huitun_latest_same_instant_attempt_count_by_account = {
            observation.platform_account_id: int(same_instant_attempt_count)
            for observation, same_instant_attempt_count in huitun_latest_rows
        }
        huitun_by_account = {
            observation.platform_account_id: observation
            for observation in huitun_accepted_observations
        }
        facts: dict[UUID, ContentActivityFact] = {}
        for account_id in (
            set(latest_by_account)
            | set(trusted_by_account)
            | set(huitun_latest_by_account)
            | set(huitun_by_account)
        ):
            latest = latest_by_account.get(account_id)
            trusted = trusted_by_account.get(account_id)
            huitun = huitun_by_account.get(account_id)
            huitun_latest = huitun_latest_by_account.get(account_id)
            facts[account_id] = ContentActivityFact(
                trusted_observation_id=trusted.id if trusted is not None else None,
                trusted_observed_at=_utc(trusted.observed_at) if trusted is not None else None,
                trusted_observation_status=(
                    trusted.observation_status if trusted is not None else None
                ),
                trusted_coverage_status=(trusted.coverage_status if trusted is not None else None),
                trusted_activity_result=(trusted.activity_result if trusted is not None else None),
                last_publication_at=(
                    _utc(trusted.last_publication_at) if trusted is not None else None
                ),
                latest_attempt_observed_at=_utc(latest.observed_at) if latest is not None else None,
                latest_attempt_observation_status=(
                    latest.observation_status if latest is not None else None
                ),
                latest_attempt_coverage_status=(
                    latest.coverage_status if latest is not None else None
                ),
                latest_attempt_activity_result=(
                    latest.activity_result if latest is not None else None
                ),
                latest_attempt_same_instant_count=(
                    latest_same_instant_attempt_count_by_account.get(account_id)
                ),
                huitun_observation_id=huitun.id if huitun is not None else None,
                huitun_observed_at=_utc(huitun.observed_at) if huitun is not None else None,
                huitun_observation_status=(
                    huitun.observation_status if huitun is not None else None
                ),
                huitun_coverage_status=(huitun.coverage_status if huitun is not None else None),
                huitun_activity_result=(huitun.activity_result if huitun is not None else None),
                huitun_last_publication_at=(
                    _utc(huitun.last_publication_at) if huitun is not None else None
                ),
                huitun_coverage_start_at=(
                    _utc(huitun.coverage_start_at) if huitun is not None else None
                ),
                huitun_coverage_end_at=(
                    _utc(huitun.coverage_end_at) if huitun is not None else None
                ),
                huitun_latest_observation_id=(
                    huitun_latest.id if huitun_latest is not None else None
                ),
                huitun_latest_observed_at=(
                    _utc(huitun_latest.observed_at) if huitun_latest is not None else None
                ),
                huitun_latest_observation_status=(
                    huitun_latest.observation_status if huitun_latest is not None else None
                ),
                huitun_latest_coverage_status=(
                    huitun_latest.coverage_status if huitun_latest is not None else None
                ),
                huitun_latest_activity_result=(
                    huitun_latest.activity_result if huitun_latest is not None else None
                ),
                huitun_latest_same_instant_count=(
                    huitun_latest_same_instant_attempt_count_by_account.get(account_id)
                ),
            )
        return facts

    @staticmethod
    def _strict_metric(value: object) -> int | None:
        return value if type(value) is int and value >= 0 else None

    @staticmethod
    def _creator_classification(
        account: InfluencerPlatformAccount,
        state: InfluencerSourceState | None,
        state_import_row: ImportRow | None,
        state_import_job: ImportJob | None,
        state_import_file: ImportJobFile | None,
    ) -> tuple[tuple[str, ...] | None, tuple[UUID, ...]]:
        """Return the source-specific creator classification with exact provenance.

        XHS retains its established ``creator_tags`` proof. Huitun Douyin's
        creator ``分类`` lives in the immutable imported row, while CollectionJob
        ``industry`` is source context and ``内容标签``/``带货类目`` must never stand
        in for either category. Both branches fail closed if their exact row
        lineage cannot be proven.
        """

        if account.platform not in {Platform.XIAOHONGSHU, Platform.DOUYIN}:
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

        if account.platform is Platform.DOUYIN:
            if (
                account.source is not DataSource.HUITUN
                or state.source is not DataSource.HUITUN
                or state_import_job is None
                or state_import_file is None
                or state_import_job.id != state.last_import_job_id
                or state_import_job.status is not ImportJobStatus.COMPLETED
                or state_import_job.confirmed_revision is None
                or state_import_job.source_type is not ImportSourceType.MANUAL_HUITUN_EXPORT
                or state_import_row.preview_revision != state_import_job.confirmed_revision
                or state_import_file.id != state_import_row.import_job_file_id
                or state_import_file.import_job_id != state_import_job.id
                or state_import_file.status is not ImportJobFileStatus.READY
            ):
                return None, ()
            raw_data = state_import_row.raw_data
            category = raw_data.get("分类") if isinstance(raw_data, dict) else None
            if type(category) is not str or not category.strip():
                return None, ()
            # Do not split or infer from free text. Huitun's 分类 is one
            # primary self-classification, normalized later by the frozen
            # taxonomy; content tags and commerce category stay unused here.
            return (category.strip(),), (state.last_import_job_id,)

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
        evaluations: Iterable[
            tuple[CandidateFactBundle, TargetingEvaluation, BuyerLeadTierDecision | None]
        ],
    ) -> tuple[int, int, int]:
        """Persist Seller MATCH/UNKNOWN and all Buyer tier rows in one transaction."""

        match_count = 0
        unknown_count = 0
        not_match_count = 0
        members: list[CandidatePoolMember] = []
        for facts, evaluation, buyer_decision in evaluations:
            if evaluation.result.value == CandidateResult.MATCH.value:
                match_count += 1
                result = CandidateResult.MATCH
            elif evaluation.result.value == CandidateResult.UNKNOWN.value:
                unknown_count += 1
                result = CandidateResult.UNKNOWN
            else:
                not_match_count += 1
                if buyer_decision is None:
                    continue
                result = CandidateResult.NOT_MATCH
            members.append(
                CandidatePoolMember(
                    run_id=run.id,
                    influencer_id=facts.influencer_id,
                    platform_account_id=facts.platform_account_id,
                    result=result,
                    buyer_lead_tier=(buyer_decision.tier if buyer_decision is not None else None),
                    buyer_relation_summary=(
                        buyer_decision.relation_summary if buyer_decision is not None else None
                    ),
                    reason_codes=[reason_code.value for reason_code in evaluation.reason_codes],
                    redacted_evidence=evaluation.redacted_evidence,
                    evidence_hash=evaluation.evidence_hash,
                )
            )
        self.session.add_all(members)
        return match_count, unknown_count, not_match_count


__all__ = [
    "BuyerProspectRuleRecord",
    "CandidatePoolPage",
    "CandidatePoolRepository",
    "CandidateRunMemberPage",
    "CandidateRunPage",
    "DEFAULT_BATCH_SIZE",
    "MAX_BATCH_SIZE",
]
