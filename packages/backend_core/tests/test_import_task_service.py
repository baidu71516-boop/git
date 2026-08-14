"""Durable import-task repository and lifecycle policy tests."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any
from uuid import uuid4

import pytest
from backend_core.config.settings import Settings
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.enums import ImportTaskKind, ImportTaskState
from backend_core.imports.models import ImportTaskRequest
from backend_core.imports.task_service import (
    ClaimStatus,
    DispatchStatus,
    ImportTaskService,
    RetryStatus,
    TaskEnvelope,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


def async_test(function: Any) -> Any:
    """Run an async scenario without adding a project-wide pytest plugin."""

    @wraps(function)
    def wrapper() -> None:
        asyncio.run(function())

    return wrapper


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 13, 4, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def task_settings(**overrides: int) -> Settings:
    values: dict[str, Any] = {
        "import_task_reconcile_batch_size": 10,
        "import_task_dispatch_max_attempts": 3,
        "import_task_dispatch_backoff_base_seconds": 2,
        "import_task_dispatch_backoff_max_seconds": 8,
        "import_task_run_backoff_base_seconds": 3,
        "import_task_run_backoff_max_seconds": 12,
        "import_task_lease_seconds": 60,
        "import_task_heartbeat_seconds": 10,
        "import_task_legacy_parse_max_run_attempts": 2,
        "import_task_file_parse_max_run_attempts": 2,
        "import_task_preview_max_run_attempts": 2,
        "import_task_confirm_max_run_attempts": 2,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@asynccontextmanager
async def task_harness(
    **settings_overrides: int,
) -> AsyncIterator[tuple[AsyncSession, ImportTaskService, MutableClock]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    clock = MutableClock()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield (
            session,
            ImportTaskService(
                session,
                task_settings(**settings_overrides),
                clock=clock.now,
            ),
            clock,
        )
    await engine.dispose()


def envelope(task: ImportTaskRequest, **changes: object) -> TaskEnvelope:
    values: dict[str, object] = {
        "task_token": task.task_token,
        "task_kind": task.task_kind,
        "import_job_id": task.import_job_id,
        "import_job_file_id": task.import_job_file_id,
        "preview_revision": task.preview_revision,
    }
    values.update(changes)
    return TaskEnvelope(**values)  # type: ignore[arg-type]


def as_utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive DateTime round trip as UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def test_settings_require_heartbeat_shorter_than_lease() -> None:
    with pytest.raises(ValueError, match="HEARTBEAT_SECONDS must be less"):
        task_settings(import_task_heartbeat_seconds=60, import_task_lease_seconds=60)

    settings = task_settings(import_task_heartbeat_seconds=59, import_task_lease_seconds=60)
    assert settings.import_task_heartbeat_seconds < settings.import_task_lease_seconds


@async_test
async def test_active_partial_uniqueness_preserves_terminal_history_and_separates_kinds() -> None:
    async with task_harness() as (session, service, _clock):
        job_id = uuid4()
        first = await service.create_request(
            task_kind=ImportTaskKind.LEGACY_PARSE,
            import_job_id=job_id,
        )
        first_token = first.task_token
        first_id = first.id
        await session.commit()

        with pytest.raises(IntegrityError):
            await service.create_request(
                task_kind=ImportTaskKind.LEGACY_PARSE,
                import_job_id=job_id,
            )
        await session.rollback()

        # Active uniqueness is kind-specific: independent job-level work can coexist.
        preview = await service.create_request(
            task_kind=ImportTaskKind.PREVIEW,
            import_job_id=job_id,
        )
        confirm = await service.create_request(
            task_kind=ImportTaskKind.CONFIRM,
            import_job_id=job_id,
            preview_revision=1,
        )
        await session.commit()
        assert preview.task_kind is ImportTaskKind.PREVIEW
        assert confirm.task_kind is ImportTaskKind.CONFIRM

        # A terminal record is immutable history and no longer blocks a fresh request.
        stored_first = await service.get_by_token(first_token, for_update=True)
        assert stored_first is not None
        stored_first.state = ImportTaskState.TERMINAL_FAILED
        stored_first.completed_at = datetime.now(UTC)
        await session.commit()
        replacement = await service.create_request(
            task_kind=ImportTaskKind.LEGACY_PARSE,
            import_job_id=job_id,
        )
        await session.commit()

        history = list(
            await session.scalars(
                select(ImportTaskRequest)
                .where(
                    ImportTaskRequest.import_job_id == job_id,
                    ImportTaskRequest.task_kind == ImportTaskKind.LEGACY_PARSE,
                )
                .order_by(ImportTaskRequest.requested_at, ImportTaskRequest.id)
            )
        )
        assert {task.id for task in history} == {first_id, replacement.id}
        assert {task.state for task in history} == {
            ImportTaskState.TERMINAL_FAILED,
            ImportTaskState.REQUESTED,
        }


@async_test
async def test_create_find_active_and_target_validation_cover_all_kinds() -> None:
    async with task_harness() as (_session, service, _clock):
        job_id = uuid4()
        file_id = uuid4()
        tasks = [
            await service.create_request(
                task_kind=ImportTaskKind.LEGACY_PARSE,
                import_job_id=job_id,
            ),
            await service.create_request(
                task_kind=ImportTaskKind.FILE_PARSE,
                import_job_id=job_id,
                import_job_file_id=file_id,
            ),
            await service.create_request(
                task_kind=ImportTaskKind.PREVIEW,
                import_job_id=job_id,
            ),
            await service.create_request(
                task_kind=ImportTaskKind.CONFIRM,
                import_job_id=job_id,
                preview_revision=7,
            ),
        ]

        for task in tasks:
            found = await service.find_active(
                task_kind=task.task_kind,
                import_job_id=job_id,
                import_job_file_id=task.import_job_file_id,
                preview_revision=task.preview_revision,
            )
            assert found is not None
            assert found.task_token == task.task_token

        with pytest.raises(ValueError, match="file target"):
            await service.create_request(
                task_kind=ImportTaskKind.FILE_PARSE,
                import_job_id=job_id,
            )
        with pytest.raises(ValueError, match="positive preview revision"):
            await service.create_request(
                task_kind=ImportTaskKind.CONFIRM,
                import_job_id=job_id,
                preview_revision=0,
            )
        with pytest.raises(ValueError, match="job-level target"):
            await service.create_request(
                task_kind=ImportTaskKind.PREVIEW,
                import_job_id=job_id,
                import_job_file_id=file_id,
            )


@async_test
async def test_prepare_dispatch_reserves_with_bounded_retry_and_exhausts() -> None:
    async with task_harness() as (_session, service, clock):
        task = await service.create_request(
            task_kind=ImportTaskKind.PREVIEW,
            import_job_id=uuid4(),
        )

        first = await service.prepare_dispatch(task.task_token)
        assert first.status is DispatchStatus.RESERVED
        assert first.envelope == envelope(task)
        assert task.dispatch_attempts == 1
        assert task.last_dispatch_attempt_at == clock.current
        assert task.next_retry_at == clock.current + timedelta(seconds=2)

        assert (await service.prepare_dispatch(task.task_token)).status is DispatchStatus.NOT_DUE
        clock.advance(seconds=2)
        assert (await service.prepare_dispatch(task.task_token)).status is DispatchStatus.RESERVED
        assert task.dispatch_attempts == 2
        assert task.next_retry_at == clock.current + timedelta(seconds=4)
        clock.advance(seconds=4)
        assert (await service.prepare_dispatch(task.task_token)).status is DispatchStatus.RESERVED
        assert task.dispatch_attempts == 3
        clock.advance(seconds=8)
        exhausted = await service.prepare_dispatch(task.task_token)
        assert exhausted.status is DispatchStatus.EXHAUSTED
        assert task.state is ImportTaskState.TERMINAL_FAILED
        assert task.completed_at == clock.current


@async_test
async def test_claim_guards_duplicate_heartbeat_generation_and_completion() -> None:
    async with task_harness() as (_session, service, clock):
        task = await service.create_request(
            task_kind=ImportTaskKind.CONFIRM,
            import_job_id=uuid4(),
            preview_revision=2,
        )
        wrong = envelope(task, preview_revision=3)
        assert (await service.claim(wrong)).status is ClaimStatus.MISMATCH
        assert task.run_attempts == 0

        claimed = await service.claim(envelope(task))
        assert claimed.status is ClaimStatus.CLAIMED
        assert claimed.generation == 1
        assert task.state is ImportTaskState.RUNNING
        assert task.started_at == clock.current
        assert task.lease_expires_at == clock.current + timedelta(seconds=60)
        assert (await service.claim(envelope(task))).status is ClaimStatus.BUSY

        assert not await service.heartbeat(task.task_token, generation=2)
        clock.advance(seconds=10)
        assert await service.heartbeat(task.task_token, generation=1)
        await service.session.refresh(task)
        assert task.lease_expires_at is not None
        assert as_utc(task.lease_expires_at) == clock.current + timedelta(seconds=60)

        assert not await service.complete(task.task_token, generation=2)
        assert await service.complete(task.task_token, generation=1)
        await service.session.refresh(task)
        completed = await service.get_by_token(task.task_token)
        assert completed is not None
        assert completed.state is ImportTaskState.COMPLETED
        assert task.completed_at is not None
        assert as_utc(task.completed_at) == clock.current
        assert task.lease_expires_at is None
        assert not await service.complete(task.task_token, generation=1)
        assert (await service.claim(envelope(task))).status is ClaimStatus.COMPLETED


@async_test
async def test_retry_wait_is_postgres_counted_and_exhaustion_is_terminal() -> None:
    async with task_harness() as (_session, service, clock):
        task = await service.create_request(
            task_kind=ImportTaskKind.PREVIEW,
            import_job_id=uuid4(),
        )
        first_claim = await service.claim(envelope(task))
        assert first_claim.generation == 1

        retry = await service.schedule_retry(task.task_token, generation=1)
        assert retry.status is RetryStatus.SCHEDULED
        assert retry.retry_at == clock.current + timedelta(seconds=3)
        assert task.state is ImportTaskState.RETRY_WAIT
        assert (await service.claim(envelope(task))).status is ClaimStatus.NOT_DUE

        clock.advance(seconds=3)
        second_claim = await service.claim(envelope(task))
        assert second_claim.status is ClaimStatus.CLAIMED
        assert second_claim.generation == 2
        exhausted = await service.schedule_retry(task.task_token, generation=2)
        assert exhausted.status is RetryStatus.EXHAUSTED
        assert exhausted.task is not None
        assert exhausted.task.run_attempts == 2
        assert exhausted.task.state is ImportTaskState.TERMINAL_FAILED
        assert task.completed_at == clock.current
        assert (await service.claim(envelope(task))).status is ClaimStatus.INACTIVE


@async_test
async def test_cancel_only_accepts_nonrunning_active_requests() -> None:
    async with task_harness() as (_session, service, _clock):
        cancellable = await service.create_request(
            task_kind=ImportTaskKind.LEGACY_PARSE,
            import_job_id=uuid4(),
        )
        assert await service.cancel(cancellable.task_token)
        assert cancellable.state is ImportTaskState.CANCELLED
        assert cancellable.completed_at is not None
        assert await service.cancel(cancellable.task_token)

        running = await service.create_request(
            task_kind=ImportTaskKind.PREVIEW,
            import_job_id=uuid4(),
        )
        assert (await service.claim(envelope(running))).status is ClaimStatus.CLAIMED
        assert not await service.cancel(running.task_token)
        assert running.state is ImportTaskState.RUNNING


@async_test
async def test_reconcile_selects_deterministic_bounded_due_and_expired_work() -> None:
    async with task_harness(import_task_reconcile_batch_size=3) as (
        _session,
        service,
        clock,
    ):
        oldest = await service.create_request(
            task_kind=ImportTaskKind.LEGACY_PARSE,
            import_job_id=uuid4(),
        )
        expired = await service.create_request(
            task_kind=ImportTaskKind.PREVIEW,
            import_job_id=uuid4(),
        )
        due_retry = await service.create_request(
            task_kind=ImportTaskKind.FILE_PARSE,
            import_job_id=uuid4(),
            import_job_file_id=uuid4(),
        )
        future = await service.create_request(
            task_kind=ImportTaskKind.CONFIRM,
            import_job_id=uuid4(),
            preview_revision=1,
        )

        oldest.requested_at = clock.current - timedelta(seconds=40)
        expired.state = ImportTaskState.RUNNING
        expired.run_attempts = 1
        expired.started_at = clock.current - timedelta(seconds=35)
        expired.lease_expires_at = clock.current - timedelta(seconds=30)
        due_retry.state = ImportTaskState.RETRY_WAIT
        due_retry.run_attempts = 1
        due_retry.next_retry_at = clock.current - timedelta(seconds=20)
        future.next_retry_at = clock.current + timedelta(seconds=30)
        await service.session.flush()

        decision = await service.reconcile(limit=99)
        assert [item.task_token for item in decision.dispatches] == [
            oldest.task_token,
            due_retry.task_token,
        ]
        assert [item.task_token for item in decision.deferred] == [expired.task_token]
        assert decision.terminal == ()
        assert oldest.dispatch_attempts == 1
        assert due_retry.dispatch_attempts == 1
        assert expired.state is ImportTaskState.RETRY_WAIT
        assert expired.next_retry_at == clock.current + timedelta(seconds=3)
        assert future.state is ImportTaskState.REQUESTED
        assert future.dispatch_attempts == 0
