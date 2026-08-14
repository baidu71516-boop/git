"""PostgreSQL-backed persistence primitives for durable import tasks.

This repository deliberately never commits or rolls back.  Import business
state and its task request/completion must share the caller's transaction.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.imports.enums import ImportTaskKind, ImportTaskState
from backend_core.imports.models import ImportTaskRequest

ACTIVE_TASK_STATES = (
    ImportTaskState.REQUESTED,
    ImportTaskState.RUNNING,
    ImportTaskState.RETRY_WAIT,
)


class ImportTaskRepository:
    """Query and stage durable task facts without owning transaction boundaries."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        task_token: UUID,
        task_kind: ImportTaskKind,
        import_job_id: UUID,
        import_job_file_id: UUID | None,
        preview_revision: int | None,
        requested_at: datetime,
    ) -> ImportTaskRequest:
        task = ImportTaskRequest(
            task_token=task_token,
            task_kind=task_kind,
            import_job_id=import_job_id,
            import_job_file_id=import_job_file_id,
            preview_revision=preview_revision,
            state=ImportTaskState.REQUESTED,
            dispatch_attempts=0,
            run_attempts=0,
            requested_at=requested_at,
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def get_by_token(
        self,
        task_token: UUID,
        *,
        for_update: bool = False,
    ) -> ImportTaskRequest | None:
        statement = (
            select(ImportTaskRequest)
            .where(ImportTaskRequest.task_token == task_token)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(ImportTaskRequest | None, await self.session.scalar(statement))

    async def find_active(
        self,
        *,
        task_kind: ImportTaskKind,
        import_job_id: UUID,
        import_job_file_id: UUID | None,
        preview_revision: int | None,
        for_update: bool = False,
    ) -> ImportTaskRequest | None:
        statement = (
            select(ImportTaskRequest)
            .where(
                ImportTaskRequest.task_kind == task_kind,
                ImportTaskRequest.import_job_id == import_job_id,
                ImportTaskRequest.state.in_(ACTIVE_TASK_STATES),
            )
            .execution_options(populate_existing=True)
        )
        if import_job_file_id is None:
            statement = statement.where(ImportTaskRequest.import_job_file_id.is_(None))
        else:
            statement = statement.where(ImportTaskRequest.import_job_file_id == import_job_file_id)
        if preview_revision is None:
            statement = statement.where(ImportTaskRequest.preview_revision.is_(None))
        else:
            statement = statement.where(ImportTaskRequest.preview_revision == preview_revision)
        if for_update:
            statement = statement.with_for_update()
        return cast(ImportTaskRequest | None, await self.session.scalar(statement))

    async def select_reconcilable(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[ImportTaskRequest]:
        """Lock a deterministic bounded due batch for one reconciler.

        PostgreSQL's ``SKIP LOCKED`` makes concurrent schedulers partition the
        batch instead of publishing the same token.  The visibility timestamp
        on requested rows closes both the DB-before-publish crash gap and the
        publish-but-ack-unknown duplicate-delivery case.
        """

        due_requested = and_(
            ImportTaskRequest.state == ImportTaskState.REQUESTED,
            or_(
                ImportTaskRequest.next_retry_at.is_(None),
                ImportTaskRequest.next_retry_at <= now,
            ),
        )
        due_retry = and_(
            ImportTaskRequest.state == ImportTaskState.RETRY_WAIT,
            ImportTaskRequest.next_retry_at <= now,
        )
        expired_running = and_(
            ImportTaskRequest.state == ImportTaskState.RUNNING,
            ImportTaskRequest.lease_expires_at <= now,
        )
        due_at = case(
            (
                ImportTaskRequest.state == ImportTaskState.RUNNING,
                ImportTaskRequest.lease_expires_at,
            ),
            else_=func.coalesce(
                ImportTaskRequest.next_retry_at,
                ImportTaskRequest.requested_at,
            ),
        )
        result = await self.session.scalars(
            select(ImportTaskRequest)
            .where(or_(due_requested, due_retry, expired_running))
            .order_by(due_at, ImportTaskRequest.requested_at, ImportTaskRequest.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return list(result)
