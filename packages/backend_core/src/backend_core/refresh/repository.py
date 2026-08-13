"""Persistence and deterministic candidate projection for refresh queues."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Table, and_, bindparam, case, func, or_, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend_core.imports.hashing import advisory_lock_key
from backend_core.influencers.enums import DataSource
from backend_core.influencers.freshness import FreshnessPolicy, FreshnessStatus
from backend_core.influencers.models import (
    Influencer,
    InfluencerCurrentMetrics,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)
from backend_core.influencers.repository import (
    eligible_huitun_freshness,
    followers_count_expression,
    freshness_status_expression,
    visible_influencer_criteria,
)
from backend_core.refresh.enums import (
    RefreshPriorityReason,
    RefreshQueueItemStatus,
    RefreshQueueStatus,
)
from backend_core.refresh.models import RefreshQueue, RefreshQueueItem
from backend_core.refresh.priority import evaluate_refresh_priority
from backend_core.refresh.reconciliation import RefreshReturnPreview, RefreshReturnQueueItem
from backend_core.refresh.schemas import IDENTITY_WHITESPACE

ACTIVE_ITEM_STATUSES = (
    RefreshQueueItemStatus.PENDING,
    RefreshQueueItemStatus.STALE_RETURN,
    RefreshQueueItemStatus.UNRESOLVED,
)


@dataclass(frozen=True, slots=True)
class RefreshCandidateRecord:
    influencer_id: UUID
    platform_account_id: UUID
    platform: str
    external_platform_account_id: str | None
    account_name: str
    account_handle: str | None
    profile_url: str | None
    external_source_id: str | None
    followers_count: int | None
    last_observed_at: datetime | None
    baseline_source_updated_at: datetime | None
    freshness_status: FreshnessStatus
    priority_tier: int
    priority_reasons: tuple[RefreshPriorityReason, ...]


@dataclass(frozen=True, slots=True)
class RefreshQueueSummaryRecord:
    selected: int
    unique_influencers: int
    freshness_breakdown: dict[str, int]
    priority_breakdown: dict[int, int]
    status_breakdown: dict[str, int]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _nonblank_sql(value: Any, *, dialect_name: str) -> ColumnElement[bool]:
    """Use the snapshot validator's exact Unicode-whitespace definition in SQL."""

    normalized = func.coalesce(value, "")
    trimmed = (
        func.btrim(normalized, IDENTITY_WHITESPACE)
        if dialect_name == "postgresql"
        else func.trim(normalized, IDENTITY_WHITESPACE)
    )
    return func.length(trimmed) > 0


class RefreshQueueRepository:
    """Fixed-query-count queue persistence with PostgreSQL concurrency fencing."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def _dialect_name(self) -> str:
        return self.session.get_bind().dialect.name

    async def acquire_department_creation_lock(self, department_id: UUID) -> None:
        """Serialize selection for one department while preserving cross-department work."""

        if self._dialect_name != "postgresql":
            return
        lock_key = advisory_lock_key(f"phase2:refresh-queue-department:{department_id}")
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

    async def acquire_candidate_locks(
        self,
        department_id: UUID,
        candidates: Iterable[RefreshCandidateRecord],
    ) -> None:
        """Lock selected department/account/source keys in one canonical order."""

        if self._dialect_name != "postgresql":
            return
        lock_keys = sorted(
            {
                advisory_lock_key(
                    "phase2:refresh-candidate:"
                    f"{department_id}:{candidate.platform_account_id}:{DataSource.HUITUN.value}"
                )
                for candidate in candidates
            }
        )
        if not lock_keys:
            return
        await self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock(lock_key) "
                "FROM unnest(CAST(:lock_keys AS BIGINT[])) AS lock_key ORDER BY lock_key"
            ),
            {"lock_keys": lock_keys},
        )

    async def list_candidates(
        self,
        *,
        department_id: UUID,
        as_of: datetime,
        policy: FreshnessPolicy,
        limit: int,
    ) -> list[RefreshCandidateRecord]:
        """Select account/source candidates from the company library deterministically."""

        eligible = eligible_huitun_freshness()
        status_expression = freshness_status_expression(eligible, as_of=as_of, policy=policy)
        followers_expression = followers_count_expression(self._dialect_name)

        source_state = (
            select(
                InfluencerSourceState.platform_account_id,
                func.max(InfluencerSourceState.source_updated_at).label("source_updated_at"),
            )
            .where(InfluencerSourceState.source == DataSource.HUITUN)
            .group_by(InfluencerSourceState.platform_account_id)
            .subquery("huitun_source_state_baseline")
        )
        source_identity = (
            select(
                PlatformAccountSourceIdentity.platform_account_id,
                func.min(PlatformAccountSourceIdentity.external_account_id).label(
                    "external_source_id"
                ),
            )
            .where(
                PlatformAccountSourceIdentity.source == DataSource.HUITUN,
                _nonblank_sql(
                    PlatformAccountSourceIdentity.external_account_id,
                    dialect_name=self._dialect_name,
                ),
            )
            .group_by(PlatformAccountSourceIdentity.platform_account_id)
            .subquery("huitun_source_identity_snapshot")
        )
        metrics = (
            select(
                InfluencerCurrentMetrics.platform_account_id,
                followers_expression.label("followers_count"),
            )
            .where(InfluencerCurrentMetrics.source == DataSource.HUITUN)
            .subquery("huitun_current_metrics_snapshot")
        )
        active_occupation = (
            select(RefreshQueueItem.id)
            .where(
                RefreshQueueItem.department_id == department_id,
                RefreshQueueItem.platform_account_id == InfluencerPlatformAccount.id,
                RefreshQueueItem.source == DataSource.HUITUN,
                RefreshQueueItem.status.in_(ACTIVE_ITEM_STATUSES),
            )
            .exists()
        )
        identity_present = or_(
            _nonblank_sql(
                InfluencerPlatformAccount.platform_account_id,
                dialect_name=self._dialect_name,
            ),
            _nonblank_sql(
                InfluencerPlatformAccount.profile_url,
                dialect_name=self._dialect_name,
            ),
            _nonblank_sql(
                InfluencerPlatformAccount.account_handle,
                dialect_name=self._dialect_name,
            ),
            _nonblank_sql(
                InfluencerPlatformAccount.account_name,
                dialect_name=self._dialect_name,
            ),
            _nonblank_sql(
                source_identity.c.external_source_id,
                dialect_name=self._dialect_name,
            ),
        )
        priority_tier = case(
            (status_expression == FreshnessStatus.UNKNOWN.value, 1),
            (status_expression == FreshnessStatus.VERY_STALE.value, 2),
            (status_expression == FreshnessStatus.STALE.value, 3),
            (status_expression == FreshnessStatus.AGING.value, 4),
            (
                and_(
                    status_expression == FreshnessStatus.FRESH.value,
                    metrics.c.followers_count.is_(None),
                ),
                5,
            ),
            else_=None,
        ).label("priority_tier")
        candidate_projection = (
            select(
                eligible.c.influencer_id,
                eligible.c.platform_account_id,
                InfluencerPlatformAccount.platform,
                InfluencerPlatformAccount.platform_account_id.label("external_platform_account_id"),
                InfluencerPlatformAccount.account_name,
                InfluencerPlatformAccount.account_handle,
                InfluencerPlatformAccount.profile_url,
                source_identity.c.external_source_id,
                metrics.c.followers_count,
                eligible.c.last_observed_at,
                source_state.c.source_updated_at.label("baseline_source_updated_at"),
                status_expression.label("freshness_status"),
                priority_tier,
            )
            .select_from(eligible)
            .join(Influencer, Influencer.id == eligible.c.influencer_id)
            .join(
                InfluencerPlatformAccount,
                InfluencerPlatformAccount.id == eligible.c.platform_account_id,
            )
            .outerjoin(
                source_state,
                source_state.c.platform_account_id == eligible.c.platform_account_id,
            )
            .outerjoin(
                source_identity,
                source_identity.c.platform_account_id == eligible.c.platform_account_id,
            )
            .outerjoin(
                metrics,
                metrics.c.platform_account_id == eligible.c.platform_account_id,
            )
            .where(
                *visible_influencer_criteria(),
                identity_present,
                ~active_occupation,
            )
            .subquery("refresh_candidate_projection")
        )
        rows = (
            await self.session.execute(
                select(candidate_projection)
                .where(candidate_projection.c.priority_tier.is_not(None))
                .order_by(
                    candidate_projection.c.priority_tier,
                    candidate_projection.c.last_observed_at.asc().nullsfirst(),
                    candidate_projection.c.influencer_id,
                    candidate_projection.c.platform_account_id,
                )
                .limit(limit)
            )
        ).mappings()

        records: list[RefreshCandidateRecord] = []
        for row in rows:
            followers_raw = row["followers_count"]
            followers = int(followers_raw) if followers_raw is not None else None
            observed_at = _utc(row["last_observed_at"])
            decision = evaluate_refresh_priority(
                policy=policy,
                observed_at=observed_at,
                as_of=as_of,
                followers_count=followers,
            )
            if decision is None:
                continue
            records.append(
                RefreshCandidateRecord(
                    influencer_id=row["influencer_id"],
                    platform_account_id=row["platform_account_id"],
                    platform=str(getattr(row["platform"], "value", row["platform"])),
                    external_platform_account_id=row["external_platform_account_id"],
                    account_name=row["account_name"],
                    account_handle=row["account_handle"],
                    profile_url=row["profile_url"],
                    external_source_id=row["external_source_id"],
                    followers_count=followers,
                    last_observed_at=observed_at,
                    baseline_source_updated_at=_utc(row["baseline_source_updated_at"]),
                    freshness_status=decision.freshness_status,
                    priority_tier=decision.tier,
                    priority_reasons=decision.reasons,
                )
            )
        return records

    async def list_queues(
        self,
        *,
        department_id: UUID | None,
        offset: int,
        limit: int,
    ) -> tuple[list[RefreshQueue], int]:
        criteria = (
            (RefreshQueue.department_id == department_id,) if department_id is not None else ()
        )
        total = int(
            await self.session.scalar(select(func.count(RefreshQueue.id)).where(*criteria)) or 0
        )
        queues = list(
            await self.session.scalars(
                select(RefreshQueue)
                .where(*criteria)
                .order_by(RefreshQueue.created_at.desc(), RefreshQueue.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        return queues, total

    async def get_queue(
        self,
        queue_id: UUID,
        *,
        department_id: UUID | None,
        for_update: bool = False,
    ) -> RefreshQueue | None:
        statement = select(RefreshQueue).where(RefreshQueue.id == queue_id)
        if department_id is not None:
            statement = statement.where(RefreshQueue.department_id == department_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(RefreshQueue | None, await self.session.scalar(statement))

    async def list_items(
        self,
        queue_id: UUID,
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[RefreshQueueItem], int]:
        total = int(
            await self.session.scalar(
                select(func.count(RefreshQueueItem.id)).where(RefreshQueueItem.queue_id == queue_id)
            )
            or 0
        )
        items = list(
            await self.session.scalars(
                select(RefreshQueueItem)
                .where(RefreshQueueItem.queue_id == queue_id)
                .order_by(
                    RefreshQueueItem.priority_tier,
                    RefreshQueueItem.baseline_last_observed_at.asc().nullsfirst(),
                    RefreshQueueItem.influencer_id,
                    RefreshQueueItem.platform_account_id,
                    RefreshQueueItem.id,
                )
                .offset(offset)
                .limit(limit)
            )
        )
        return items, total

    async def all_items(self, queue_id: UUID) -> list[RefreshQueueItem]:
        return list(
            await self.session.scalars(
                select(RefreshQueueItem)
                .where(RefreshQueueItem.queue_id == queue_id)
                .order_by(
                    RefreshQueueItem.priority_tier,
                    RefreshQueueItem.baseline_last_observed_at.asc().nullsfirst(),
                    RefreshQueueItem.influencer_id,
                    RefreshQueueItem.platform_account_id,
                    RefreshQueueItem.id,
                )
            )
        )

    async def list_reconciliation_items(
        self,
        queue_id: UUID,
        *,
        for_update: bool = False,
    ) -> list[RefreshReturnQueueItem]:
        """Read or lock the minimal Item facts needed for return reconciliation.

        This projection deliberately omits ``identity_snapshot`` so neither
        Preview nor Confirm can accidentally turn Queue export evidence into a
        Hard Match or fallback identity source.
        """

        statement = (
            select(
                RefreshQueueItem.queue_id,
                RefreshQueueItem.id.label("queue_item_id"),
                RefreshQueueItem.platform_account_id,
                RefreshQueueItem.source,
                RefreshQueueItem.baseline_last_observed_at,
                RefreshQueueItem.status,
            )
            .where(RefreshQueueItem.queue_id == queue_id)
            .order_by(
                RefreshQueueItem.platform_account_id,
                RefreshQueueItem.source,
                RefreshQueueItem.id,
            )
        )
        if for_update:
            statement = statement.with_for_update()
        rows = (await self.session.execute(statement)).mappings()
        return [
            RefreshReturnQueueItem(
                queue_id=row["queue_id"],
                queue_item_id=row["queue_item_id"],
                platform_account_id=row["platform_account_id"],
                source=row["source"],
                baseline_last_observed_at=_utc(row["baseline_last_observed_at"]),
                status=row["status"],
            )
            for row in rows
        ]

    async def apply_return_fulfillment(
        self,
        queue_id: UUID,
        *,
        import_job_id: UUID,
        preview: RefreshReturnPreview,
        now: datetime,
    ) -> bool:
        """Apply locked return decisions and aggregate Queue completion.

        The caller must have built ``preview`` from ``list_reconciliation_items``
        with ``for_update=True`` in the same transaction.  Core executemany
        updates preserve the projection boundary: ``identity_snapshot`` is
        never selected or used while fulfillment lineage is written.
        """

        claimed_items = preview.claimed_items
        if claimed_items:
            fulfilled_statuses = {
                RefreshQueueItemStatus.FULFILLED_CHANGED,
                RefreshQueueItemStatus.FULFILLED_NO_CHANGE,
            }
            parameters = []
            for decision in claimed_items:
                fulfilled = decision.expected_status in fulfilled_statuses
                assert decision.claimant_locator is not None
                parameters.append(
                    {
                        "return_item_id": decision.queue_item_id,
                        "return_queue_id": queue_id,
                        "return_current_status": decision.current_status,
                        "return_next_status": decision.expected_status,
                        "return_job_id": import_job_id,
                        "return_row_id": decision.claimant_locator.import_row_id,
                        "return_fulfilled_job_id": import_job_id if fulfilled else None,
                        "return_fulfilled_row_id": (
                            decision.claimant_locator.import_row_id if fulfilled else None
                        ),
                        "return_fulfilled_at": now if fulfilled else None,
                        "return_updated_at": now,
                    }
                )
            item_table = cast(Table, RefreshQueueItem.__table__)
            statement = (
                update(item_table)
                .where(
                    item_table.c.id == bindparam("return_item_id"),
                    item_table.c.queue_id == bindparam("return_queue_id"),
                    item_table.c.status == bindparam("return_current_status"),
                )
                .values(
                    status=bindparam("return_next_status"),
                    fulfilled_import_job_id=bindparam("return_fulfilled_job_id"),
                    fulfilled_import_row_id=bindparam("return_fulfilled_row_id"),
                    fulfilled_at=bindparam("return_fulfilled_at"),
                    last_return_import_job_id=bindparam("return_job_id"),
                    last_return_import_row_id=bindparam("return_row_id"),
                    updated_at=bindparam("return_updated_at"),
                )
                .execution_options(synchronize_session=False)
            )
            result = await self.session.execute(statement, parameters)
            if int(cast(CursorResult[Any], result).rowcount or 0) != len(parameters):
                raise RuntimeError("locked Refresh Queue Item state changed during fulfillment")

        fulfilled_statuses = {
            RefreshQueueItemStatus.FULFILLED_CHANGED,
            RefreshQueueItemStatus.FULFILLED_NO_CHANGE,
        }
        should_complete = bool(preview.item_evidence) and all(
            item.expected_status in fulfilled_statuses for item in preview.item_evidence
        )
        if not should_complete:
            return False
        queue_result = await self.session.execute(
            update(RefreshQueue)
            .where(
                RefreshQueue.id == queue_id,
                RefreshQueue.status.in_((RefreshQueueStatus.OPEN, RefreshQueueStatus.EXPORTED)),
            )
            .values(
                status=RefreshQueueStatus.COMPLETED,
                completed_at=now,
                cancelled_at=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if int(cast(CursorResult[Any], queue_result).rowcount or 0) != 1:
            raise RuntimeError("locked Refresh Queue state changed during completion")
        return True

    async def summary(self, queue_id: UUID) -> RefreshQueueSummaryRecord:
        status_rows = (
            await self.session.execute(
                select(RefreshQueueItem.status, func.count(RefreshQueueItem.id))
                .where(RefreshQueueItem.queue_id == queue_id)
                .group_by(RefreshQueueItem.status)
            )
        ).all()
        priority_rows = (
            await self.session.execute(
                select(RefreshQueueItem.priority_tier, func.count(RefreshQueueItem.id))
                .where(RefreshQueueItem.queue_id == queue_id)
                .group_by(RefreshQueueItem.priority_tier)
                .order_by(RefreshQueueItem.priority_tier)
            )
        ).all()
        selected, unique_influencers = (
            await self.session.execute(
                select(
                    func.count(RefreshQueueItem.id),
                    func.count(func.distinct(RefreshQueueItem.influencer_id)),
                ).where(RefreshQueueItem.queue_id == queue_id)
            )
        ).one()
        freshness: Counter[str] = Counter()
        reasons = await self.session.scalars(
            select(RefreshQueueItem.priority_reasons).where(RefreshQueueItem.queue_id == queue_id)
        )
        reason_to_status = {
            RefreshPriorityReason.FRESHNESS_UNKNOWN.value: FreshnessStatus.UNKNOWN.value,
            RefreshPriorityReason.VERY_STALE.value: FreshnessStatus.VERY_STALE.value,
            RefreshPriorityReason.STALE.value: FreshnessStatus.STALE.value,
            RefreshPriorityReason.AGING.value: FreshnessStatus.AGING.value,
        }
        for raw_reasons in reasons:
            primary = next(
                (reason_to_status[reason] for reason in raw_reasons if reason in reason_to_status),
                FreshnessStatus.FRESH.value,
            )
            freshness[primary] += 1
        return RefreshQueueSummaryRecord(
            selected=int(selected or 0),
            unique_influencers=int(unique_influencers or 0),
            freshness_breakdown=dict(sorted(freshness.items())),
            priority_breakdown={int(tier): int(count) for tier, count in priority_rows},
            status_breakdown=dict(
                sorted((status.value, int(count)) for status, count in status_rows)
            ),
        )

    async def cancel_active_items(self, queue_id: UUID) -> int:
        """Release every unfinished candidate in one set-based statement."""

        result = await self.session.scalars(
            update(RefreshQueueItem)
            .where(
                RefreshQueueItem.queue_id == queue_id,
                RefreshQueueItem.status.in_(ACTIVE_ITEM_STATUSES),
            )
            .values(status=RefreshQueueItemStatus.CANCELLED)
            .returning(RefreshQueueItem.id)
        )
        return len(result.all())


__all__ = [
    "ACTIVE_ITEM_STATUSES",
    "RefreshCandidateRecord",
    "RefreshQueueRepository",
    "RefreshQueueSummaryRecord",
]
