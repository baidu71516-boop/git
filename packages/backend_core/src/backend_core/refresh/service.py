"""Authenticated, atomic orchestration for department-owned refresh queues."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
from backend_core.auth.enums import DepartmentStatus, Role
from backend_core.auth.repository import AuthRepository
from backend_core.auth.service import AuthContext
from backend_core.config.settings import Settings
from backend_core.influencers.enums import DataSource
from backend_core.influencers.freshness import FreshnessPolicy, FreshnessStatus
from backend_core.refresh.csv_export import (
    RefreshQueueCSVRow,
    export_refresh_queue_csv,
)
from backend_core.refresh.enums import RefreshQueueItemStatus, RefreshQueueStatus
from backend_core.refresh.models import RefreshQueue, RefreshQueueItem
from backend_core.refresh.priority import POLICY_VERSION
from backend_core.refresh.repository import RefreshCandidateRecord, RefreshQueueRepository
from backend_core.refresh.schemas import (
    CriteriaSnapshot,
    IdentitySnapshot,
    RefreshQueueCreateInput,
    RefreshQueueDetailResponse,
    RefreshQueueItemListQuery,
    RefreshQueueItemPage,
    RefreshQueueItemPublic,
    RefreshQueueListPage,
    RefreshQueueListQuery,
    RefreshQueuePublic,
    RefreshQueueSummary,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class RefreshQueueError(Exception):
    """HTTP-independent refresh queue error with a stable public contract."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ExportResult:
    filename: str
    content: str


_TIER_FRESHNESS = {
    1: FreshnessStatus.UNKNOWN,
    2: FreshnessStatus.VERY_STALE,
    3: FreshnessStatus.STALE,
    4: FreshnessStatus.AGING,
    5: FreshnessStatus.FRESH,
}


def _as_utc(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite drops timezone metadata from DateTime columns in local tests;
        # persisted application timestamps are defined as UTC.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _clock_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock value must be timezone-aware")
    return value.astimezone(UTC)


def _optional_utc(value: datetime | None, *, name: str) -> datetime | None:
    return None if value is None else _as_utc(value, name=name)


def _model_id(model: object, *, code: str, message: str) -> UUID:
    identity = getattr(model, "id", None)
    if not isinstance(identity, UUID):
        raise RefreshQueueError(409, code, message)
    return identity


class RefreshQueueService:
    """Own queue selection, snapshots, state transitions, and safe exports."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        freshness_policy: FreshnessPolicy | None = None,
        clock: Clock | None = None,
        repository: RefreshQueueRepository | None = None,
    ) -> None:
        self.session = session
        self.repository = repository or RefreshQueueRepository(session)
        self.auth_repository = AuthRepository(session)
        self.audit = AuditRepository(session)
        if freshness_policy is None:
            runtime_settings = settings or Settings()
            freshness_policy = FreshnessPolicy.from_day_thresholds(
                runtime_settings.freshness_fresh_days,
                runtime_settings.freshness_aging_days,
                runtime_settings.freshness_stale_days,
            )
        self.freshness_policy = freshness_policy
        self.clock = clock or SystemClock()

    @staticmethod
    def _require_mutation(context: AuthContext) -> UUID:
        if context.operator is None:
            raise RefreshQueueError(409, "OPERATOR_REQUIRED", "Select an operator first")
        if context.role is Role.VIEWER:
            raise RefreshQueueError(403, "PERMISSION_DENIED", "Viewer role is read-only")
        return _model_id(
            context.operator,
            code="OPERATOR_REQUIRED",
            message="Select an operator first",
        )

    @staticmethod
    def _context_department_id(context: AuthContext) -> UUID:
        return _model_id(
            context.department,
            code="DEPARTMENT_REQUIRED",
            message="Authenticated department is required",
        )

    @classmethod
    def _read_department_scope(cls, context: AuthContext) -> UUID | None:
        if context.role is Role.SUPER_ADMIN:
            return None
        return cls._context_department_id(context)

    async def _target_department_id(
        self,
        context: AuthContext,
        requested_department_id: UUID | None,
    ) -> UUID:
        own_department_id = self._context_department_id(context)
        target_department_id = requested_department_id or own_department_id
        if context.role is not Role.SUPER_ADMIN and target_department_id != own_department_id:
            raise RefreshQueueError(
                403,
                "PERMISSION_DENIED",
                "Department data scope denied",
            )
        target = await self.auth_repository.get_department(target_department_id)
        if target is None or target.status is not DepartmentStatus.ACTIVE:
            raise RefreshQueueError(404, "DEPARTMENT_NOT_FOUND", "Department not found")
        return target_department_id

    async def create_queue(
        self,
        context: AuthContext,
        create_input: RefreshQueueCreateInput,
        ip: str,
        user_agent: str,
    ) -> RefreshQueueDetailResponse:
        """Atomically freeze one deterministic candidate selection and its audit."""

        operator_id = self._require_mutation(context)
        try:
            department_id = await self._target_department_id(
                context,
                create_input.department_id,
            )
            as_of = _clock_utc(self.clock.now())
            criteria = CriteriaSnapshot.from_policy(
                create_input=create_input,
                policy=self.freshness_policy,
            )

            await self.repository.acquire_department_creation_lock(department_id)
            candidates = await self.repository.list_candidates(
                department_id=department_id,
                as_of=as_of,
                policy=self.freshness_policy,
                limit=create_input.requested_limit,
            )
            await self.repository.acquire_candidate_locks(department_id, candidates)
            # Re-evaluate after the locks. Any newly occupied rows disappear and
            # the stable LIMIT naturally fills from the next eligible candidates.
            candidates = await self.repository.list_candidates(
                department_id=department_id,
                as_of=as_of,
                policy=self.freshness_policy,
                limit=create_input.requested_limit,
            )

            queue = RefreshQueue(
                department_id=department_id,
                created_by_operator_id=operator_id,
                status=RefreshQueueStatus.OPEN,
                as_of=as_of,
                requested_limit=create_input.requested_limit,
                today_total_limit=create_input.today_total_limit,
                refresh_limit=create_input.refresh_limit,
                policy_version=POLICY_VERSION,
                criteria_snapshot=criteria.model_dump(mode="json"),
                exported_at=None,
                completed_at=None,
                cancelled_at=None,
            )
            self.session.add(queue)
            await self.session.flush()
            self.session.add_all([self._new_item(queue, candidate) for candidate in candidates])
            await self.session.flush()

            detail = await self._detail(queue)
            self.audit.add(
                action=AuditAction.REFRESH_QUEUE_CREATED,
                result=AuditResult.SUCCESS,
                department_id=department_id,
                operator_id=operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="refresh_queue",
                entity_id=queue.id,
                after={
                    "status": RefreshQueueStatus.OPEN.value,
                    "requested_limit": create_input.requested_limit,
                    "selected_count": len(candidates),
                    "policy_version": POLICY_VERSION,
                },
            )
            await self.session.commit()
            return detail
        except BaseException:
            await self.session.rollback()
            raise

    async def list_queues(
        self,
        context: AuthContext,
        query: RefreshQueueListQuery,
    ) -> RefreshQueueListPage:
        queues, total = await self.repository.list_queues(
            department_id=self._read_department_scope(context),
            offset=query.offset,
            limit=query.limit,
        )
        return RefreshQueueListPage(
            items=[self._queue_public(queue) for queue in queues],
            total=total,
            offset=query.offset,
            limit=query.limit,
        )

    async def get_queue_detail(
        self,
        context: AuthContext,
        queue_id: UUID,
    ) -> RefreshQueueDetailResponse:
        queue = await self._scoped_queue(context, queue_id)
        return await self._detail(queue)

    async def list_queue_items(
        self,
        context: AuthContext,
        queue_id: UUID,
        query: RefreshQueueItemListQuery,
    ) -> RefreshQueueItemPage:
        queue = await self._scoped_queue(context, queue_id)
        items, total = await self.repository.list_items(
            queue.id,
            offset=query.offset,
            limit=query.limit,
        )
        return RefreshQueueItemPage(
            items=[self._item_public(item) for item in items],
            total=total,
            offset=query.offset,
            limit=query.limit,
        )

    async def export_queue(
        self,
        context: AuthContext,
        queue_id: UUID,
        ip: str,
        user_agent: str,
    ) -> ExportResult:
        operator_id = self._require_mutation(context)
        try:
            queue = await self._scoped_queue(context, queue_id, for_update=True)
            if queue.status not in (RefreshQueueStatus.OPEN, RefreshQueueStatus.EXPORTED):
                raise RefreshQueueError(
                    409,
                    "REFRESH_QUEUE_NOT_EXPORTABLE",
                    "Refresh queue cannot be exported in its current status",
                )
            items = await self.repository.all_items(queue.id)
            csv_rows = [self._csv_row(item) for item in items]
            content = export_refresh_queue_csv(csv_rows)
            first_export = queue.status is RefreshQueueStatus.OPEN
            if first_export:
                queue.status = RefreshQueueStatus.EXPORTED
                queue.exported_at = _clock_utc(self.clock.now())
            await self.session.flush()
            self.audit.add(
                action=AuditAction.REFRESH_QUEUE_EXPORTED,
                result=AuditResult.SUCCESS,
                department_id=queue.department_id,
                operator_id=operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="refresh_queue",
                entity_id=queue.id,
                after={
                    "status": RefreshQueueStatus.EXPORTED.value,
                    "item_count": len(items),
                    "first_export": first_export,
                },
            )
            await self.session.commit()
            return ExportResult(
                filename=f"refresh-queue-{queue.id}.csv",
                content=content,
            )
        except BaseException:
            await self.session.rollback()
            raise

    async def cancel_queue(
        self,
        context: AuthContext,
        queue_id: UUID,
        ip: str,
        user_agent: str,
    ) -> RefreshQueueDetailResponse:
        operator_id = self._require_mutation(context)
        try:
            queue = await self._scoped_queue(context, queue_id, for_update=True)
            if queue.status not in (RefreshQueueStatus.OPEN, RefreshQueueStatus.EXPORTED):
                raise RefreshQueueError(
                    409,
                    "REFRESH_QUEUE_NOT_CANCELLABLE",
                    "Refresh queue cannot be cancelled in its current status",
                )
            previous_status = queue.status
            cancelled_items = await self.repository.cancel_active_items(queue.id)
            queue.status = RefreshQueueStatus.CANCELLED
            queue.cancelled_at = _clock_utc(self.clock.now())
            await self.session.flush()
            # SQLAlchemy expires server/on-update columns after the UPDATE;
            # refresh them inside the async greenlet before building a DTO.
            await self.session.refresh(queue)
            detail = await self._detail(queue)
            self.audit.add(
                action=AuditAction.REFRESH_QUEUE_CANCELLED,
                result=AuditResult.SUCCESS,
                department_id=queue.department_id,
                operator_id=operator_id,
                ip=ip,
                user_agent=user_agent,
                entity_type="refresh_queue",
                entity_id=queue.id,
                before={"status": previous_status.value},
                after={
                    "status": RefreshQueueStatus.CANCELLED.value,
                    "cancelled_item_count": cancelled_items,
                },
            )
            await self.session.commit()
            return detail
        except BaseException:
            await self.session.rollback()
            raise

    @staticmethod
    def _new_item(
        queue: RefreshQueue,
        candidate: RefreshCandidateRecord,
    ) -> RefreshQueueItem:
        identity = IdentitySnapshot(
            platform=candidate.platform,
            account_name=candidate.account_name,
            platform_account_id=candidate.external_platform_account_id,
            account_handle=candidate.account_handle,
            profile_url=candidate.profile_url,
            external_source_id=candidate.external_source_id,
            followers_count=candidate.followers_count,
        )
        return RefreshQueueItem(
            department_id=queue.department_id,
            queue_id=queue.id,
            influencer_id=candidate.influencer_id,
            platform_account_id=candidate.platform_account_id,
            source=DataSource.HUITUN,
            priority_tier=candidate.priority_tier,
            priority_reasons=[reason.value for reason in candidate.priority_reasons],
            identity_snapshot=identity.model_dump(mode="json"),
            baseline_last_observed_at=candidate.last_observed_at,
            baseline_source_updated_at=candidate.baseline_source_updated_at,
            status=RefreshQueueItemStatus.PENDING,
            fulfilled_import_job_id=None,
            fulfilled_import_row_id=None,
            fulfilled_at=None,
            last_return_import_job_id=None,
            last_return_import_row_id=None,
        )

    async def _scoped_queue(
        self,
        context: AuthContext,
        queue_id: UUID,
        *,
        for_update: bool = False,
    ) -> RefreshQueue:
        queue = await self.repository.get_queue(
            queue_id,
            department_id=self._read_department_scope(context),
            for_update=for_update,
        )
        if queue is None:
            raise RefreshQueueError(404, "REFRESH_QUEUE_NOT_FOUND", "Refresh queue not found")
        return queue

    async def _detail(self, queue: RefreshQueue) -> RefreshQueueDetailResponse:
        summary_record = await self.repository.summary(queue.id)
        return RefreshQueueDetailResponse(
            queue=self._queue_public(queue),
            summary=RefreshQueueSummary(
                requested=queue.requested_limit,
                selected=summary_record.selected,
                unique_influencers=summary_record.unique_influencers,
                freshness_breakdown=summary_record.freshness_breakdown,
                priority_breakdown=summary_record.priority_breakdown,
                status_breakdown=summary_record.status_breakdown,
            ),
        )

    @staticmethod
    def _queue_public(queue: RefreshQueue) -> RefreshQueuePublic:
        return RefreshQueuePublic(
            id=queue.id,
            department_id=queue.department_id,
            created_by_operator_id=queue.created_by_operator_id,
            status=queue.status,
            as_of=_as_utc(queue.as_of, name="queue as_of"),
            requested_limit=queue.requested_limit,
            today_total_limit=queue.today_total_limit,
            refresh_limit=queue.refresh_limit,
            policy_version=queue.policy_version,
            criteria_snapshot=queue.criteria_snapshot,
            created_at=_as_utc(queue.created_at, name="queue created_at"),
            updated_at=_as_utc(queue.updated_at, name="queue updated_at"),
            exported_at=_optional_utc(queue.exported_at, name="queue exported_at"),
            completed_at=_optional_utc(queue.completed_at, name="queue completed_at"),
            cancelled_at=_optional_utc(queue.cancelled_at, name="queue cancelled_at"),
        )

    @staticmethod
    def _item_public(item: RefreshQueueItem) -> RefreshQueueItemPublic:
        return RefreshQueueItemPublic(
            id=item.id,
            department_id=item.department_id,
            queue_id=item.queue_id,
            influencer_id=item.influencer_id,
            platform_account_id=item.platform_account_id,
            source=item.source,
            priority_tier=item.priority_tier,
            priority_reasons=tuple(item.priority_reasons),
            identity_snapshot=item.identity_snapshot,
            baseline_last_observed_at=_optional_utc(
                item.baseline_last_observed_at,
                name="baseline_last_observed_at",
            ),
            baseline_source_updated_at=_optional_utc(
                item.baseline_source_updated_at,
                name="baseline_source_updated_at",
            ),
            status=item.status,
            fulfilled_import_job_id=item.fulfilled_import_job_id,
            fulfilled_import_row_id=item.fulfilled_import_row_id,
            fulfilled_at=_optional_utc(item.fulfilled_at, name="item fulfilled_at"),
            last_return_import_job_id=item.last_return_import_job_id,
            last_return_import_row_id=item.last_return_import_row_id,
            created_at=_as_utc(item.created_at, name="item created_at"),
            updated_at=_as_utc(item.updated_at, name="item updated_at"),
        )

    @staticmethod
    def _csv_row(item: RefreshQueueItem) -> RefreshQueueCSVRow:
        return RefreshQueueCSVRow(
            influencer_id=item.influencer_id,
            identity_snapshot=IdentitySnapshot.model_validate(item.identity_snapshot),
            baseline_last_observed_at=_optional_utc(
                item.baseline_last_observed_at,
                name="baseline_last_observed_at",
            ),
            freshness_status=_TIER_FRESHNESS[item.priority_tier],
            priority_tier=item.priority_tier,
            priority_reasons=tuple(item.priority_reasons),
        )


__all__ = [
    "Clock",
    "ExportResult",
    "RefreshQueueError",
    "RefreshQueueService",
    "SystemClock",
]
