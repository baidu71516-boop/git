"""Durable import-task lifecycle, lease, retry, and reconciliation policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend_core.config.settings import Settings
from backend_core.imports.enums import ImportTaskKind, ImportTaskState
from backend_core.imports.models import ImportTaskRequest
from backend_core.imports.task_repository import ImportTaskRepository

TaskToken = UUID | str


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class TaskEnvelope:
    """The complete and intentionally ID-only broker contract."""

    task_token: UUID
    task_kind: ImportTaskKind
    import_job_id: UUID
    import_job_file_id: UUID | None = None
    preview_revision: int | None = None

    def __post_init__(self) -> None:
        if self.task_kind is ImportTaskKind.FILE_PARSE:
            if self.import_job_file_id is None or self.preview_revision is not None:
                raise ValueError("file_parse payload requires a file ID and no revision")
            return
        if self.task_kind is ImportTaskKind.CONFIRM:
            if (
                self.import_job_file_id is not None
                or self.preview_revision is None
                or self.preview_revision < 1
            ):
                raise ValueError("confirm payload requires a positive revision and no file ID")
            return
        if self.import_job_file_id is not None or self.preview_revision is not None:
            raise ValueError(f"{self.task_kind.value} payload is job-only without a revision")

    @classmethod
    def from_task(cls, task: ImportTaskRequest) -> TaskEnvelope:
        return cls(
            task_token=task.task_token,
            task_kind=task.task_kind,
            import_job_id=task.import_job_id,
            import_job_file_id=task.import_job_file_id,
            preview_revision=task.preview_revision,
        )

    @classmethod
    def from_payload(
        cls,
        *,
        task_token: TaskToken,
        task_kind: ImportTaskKind | str,
        import_job_id: UUID | str,
        import_job_file_id: UUID | str | None = None,
        preview_revision: int | None = None,
    ) -> TaskEnvelope:
        return cls(
            task_token=_require_uuid(task_token, "task_token"),
            task_kind=ImportTaskKind(task_kind),
            import_job_id=_require_uuid(import_job_id, "import_job_id"),
            import_job_file_id=(
                _require_uuid(import_job_file_id, "import_job_file_id")
                if import_job_file_id is not None
                else None
            ),
            preview_revision=preview_revision,
        )

    def broker_kwargs(self) -> dict[str, str | int]:
        """Return only frozen identifiers suitable for a Celery message."""

        payload: dict[str, str | int] = {
            "task_token": str(self.task_token),
            "import_job_id": str(self.import_job_id),
        }
        if self.import_job_file_id is not None:
            payload["import_job_file_id"] = str(self.import_job_file_id)
        if self.preview_revision is not None:
            payload["preview_revision"] = self.preview_revision
        return payload


class DispatchStatus(StrEnum):
    RESERVED = "reserved"
    NOT_DUE = "not_due"
    BUSY = "busy"
    COMPLETED = "completed"
    INACTIVE = "inactive"
    EXHAUSTED = "exhausted"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class DispatchDecision:
    status: DispatchStatus
    task: ImportTaskRequest | None
    envelope: TaskEnvelope | None = None


class ClaimStatus(StrEnum):
    CLAIMED = "claimed"
    COMPLETED = "completed"
    BUSY = "busy"
    NOT_DUE = "not_due"
    EXPIRED = "expired"
    INACTIVE = "inactive"
    EXHAUSTED = "exhausted"
    MISMATCH = "mismatch"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class ClaimDecision:
    status: ClaimStatus
    task: ImportTaskRequest | None
    generation: int | None = None


class RetryStatus(StrEnum):
    SCHEDULED = "scheduled"
    EXHAUSTED = "exhausted"
    LOST = "lost"
    COMPLETED = "completed"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class RetryDecision:
    status: RetryStatus
    task: ImportTaskRequest | None
    retry_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReconcileDecision:
    """Changes to commit before dispatching any returned envelope."""

    dispatches: tuple[TaskEnvelope, ...]
    terminal: tuple[TaskEnvelope, ...]
    deferred: tuple[TaskEnvelope, ...]


class ImportTaskService:
    """Guard every task transition with PostgreSQL facts.

    Methods stage changes in ``session`` but never commit or roll back.  This is
    what allows a Job/File transition plus task creation, and business DML plus
    task completion, to be atomic.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.repository = ImportTaskRepository(session)
        # Production PostgreSQL uses its own wall clock. Supplying ``clock`` is
        # an explicit deterministic-test seam; SQLite uses the process clock.
        self.clock = clock

    async def create_request(
        self,
        *,
        task_kind: ImportTaskKind,
        import_job_id: UUID,
        import_job_file_id: UUID | None = None,
        preview_revision: int | None = None,
        task_token: UUID | None = None,
    ) -> ImportTaskRequest:
        self._validate_target(
            task_kind=task_kind,
            import_job_file_id=import_job_file_id,
            preview_revision=preview_revision,
        )
        return await self.repository.create(
            task_token=task_token or uuid4(),
            task_kind=task_kind,
            import_job_id=import_job_id,
            import_job_file_id=import_job_file_id,
            preview_revision=preview_revision,
            requested_at=await self._current_time(),
        )

    async def get_by_token(
        self,
        task_token: TaskToken,
        *,
        for_update: bool = False,
    ) -> ImportTaskRequest | None:
        parsed_token = _optional_uuid(task_token)
        if parsed_token is None:
            return None
        return await self.repository.get_by_token(parsed_token, for_update=for_update)

    async def find_active(
        self,
        *,
        task_kind: ImportTaskKind,
        import_job_id: UUID,
        import_job_file_id: UUID | None = None,
        preview_revision: int | None = None,
        for_update: bool = False,
    ) -> ImportTaskRequest | None:
        self._validate_target(
            task_kind=task_kind,
            import_job_file_id=import_job_file_id,
            preview_revision=preview_revision,
        )
        return await self.repository.find_active(
            task_kind=task_kind,
            import_job_id=import_job_id,
            import_job_file_id=import_job_file_id,
            preview_revision=preview_revision,
            for_update=for_update,
        )

    async def prepare_dispatch(self, task_token: TaskToken) -> DispatchDecision:
        task = await self.get_by_token(task_token, for_update=True)
        if task is None:
            return DispatchDecision(DispatchStatus.MISSING, None)
        decision = self._reserve_locked(task, await self._current_time())
        await self.session.flush()
        return decision

    async def claim(self, envelope: TaskEnvelope) -> ClaimDecision:
        task = await self.repository.get_by_token(envelope.task_token, for_update=True)
        if task is None:
            return ClaimDecision(ClaimStatus.MISSING, None)
        if not self._matches(task, envelope):
            return ClaimDecision(ClaimStatus.MISMATCH, task)

        now = await self._current_time()
        if task.state is ImportTaskState.COMPLETED:
            return ClaimDecision(ClaimStatus.COMPLETED, task)
        if task.state in {ImportTaskState.TERMINAL_FAILED, ImportTaskState.CANCELLED}:
            return ClaimDecision(ClaimStatus.INACTIVE, task)
        if task.state is ImportTaskState.RUNNING:
            if self._is_after(task.lease_expires_at, now):
                return ClaimDecision(ClaimStatus.BUSY, task)
            return ClaimDecision(ClaimStatus.EXPIRED, task)
        if task.state is ImportTaskState.RETRY_WAIT and self._is_after(task.next_retry_at, now):
            return ClaimDecision(ClaimStatus.NOT_DUE, task)
        if task.run_attempts >= self._max_run_attempts(task.task_kind):
            self._set_terminal(task, now)
            await self.session.flush()
            return ClaimDecision(ClaimStatus.EXHAUSTED, task)

        task.state = ImportTaskState.RUNNING
        task.run_attempts += 1
        task.started_at = task.started_at or now
        task.lease_expires_at = now + timedelta(seconds=self.settings.import_task_lease_seconds)
        task.next_retry_at = None
        task.completed_at = None
        await self.session.flush()
        return ClaimDecision(ClaimStatus.CLAIMED, task, generation=task.run_attempts)

    async def heartbeat(self, task_token: TaskToken, generation: int) -> bool:
        parsed_token = _optional_uuid(task_token)
        if parsed_token is None or generation < 1:
            return False
        # Lock first and read the authoritative wall clock second. A single
        # UPDATE may evaluate even a volatile clock_timestamp() before waiting
        # on a row lock, which could revive a lease that expired while blocked.
        task = await self.repository.get_by_token(parsed_token, for_update=True)
        now = await self._current_time()
        if (
            task is None
            or task.state is not ImportTaskState.RUNNING
            or task.run_attempts != generation
            or not self._is_after(task.lease_expires_at, now)
        ):
            return False
        task.lease_expires_at = now + timedelta(seconds=self.settings.import_task_lease_seconds)
        task.updated_at = now
        await self.session.flush()
        return True

    async def complete(self, task_token: TaskToken, generation: int) -> bool:
        """Complete only the live generation; call inside the business transaction."""

        parsed_token = _optional_uuid(task_token)
        if parsed_token is None or generation < 1:
            return False
        task = await self.repository.get_by_token(parsed_token, for_update=True)
        now = await self._current_time()
        if (
            task is None
            or task.state is not ImportTaskState.RUNNING
            or task.run_attempts != generation
            or not self._is_after(task.lease_expires_at, now)
        ):
            # A completed row is not proof that *this* generation still owns
            # the business commit. Broker replay is absorbed by claim() before
            # a processor starts; completion must be a strict live-lease CAS.
            return False
        task.state = ImportTaskState.COMPLETED
        task.completed_at = now
        task.lease_expires_at = None
        task.next_retry_at = None
        task.updated_at = now
        await self.session.flush()
        return True

    async def schedule_retry(self, task_token: TaskToken, generation: int) -> RetryDecision:
        task = await self.get_by_token(task_token, for_update=True)
        if task is None:
            return RetryDecision(RetryStatus.MISSING, None)
        if task.state is ImportTaskState.COMPLETED:
            return RetryDecision(RetryStatus.COMPLETED, task)
        if task.state is not ImportTaskState.RUNNING or task.run_attempts != generation:
            return RetryDecision(RetryStatus.LOST, task)

        now = await self._current_time()
        if not self._is_after(task.lease_expires_at, now):
            return RetryDecision(RetryStatus.LOST, task)
        if task.run_attempts >= self._max_run_attempts(task.task_kind):
            self._set_terminal(task, now)
            await self.session.flush()
            return RetryDecision(RetryStatus.EXHAUSTED, task)
        retry_at = now + timedelta(seconds=self._run_backoff(task.run_attempts))
        self._set_retry_wait(task, retry_at)
        await self.session.flush()
        return RetryDecision(RetryStatus.SCHEDULED, task, retry_at=retry_at)

    async def terminal_fail(
        self,
        task_token: TaskToken,
        *,
        generation: int | None = None,
    ) -> bool:
        task = await self.get_by_token(task_token, for_update=True)
        if task is None:
            return False
        if task.state is ImportTaskState.TERMINAL_FAILED:
            # Idempotent dispatch exhaustion may probe a terminal row without a
            # run generation.  A worker generation, however, no longer owns the
            # terminal transition once another actor has settled the request.
            return generation is None
        if task.state in {ImportTaskState.COMPLETED, ImportTaskState.CANCELLED}:
            return False
        now = await self._current_time()
        if task.state is ImportTaskState.RUNNING:
            if (
                generation is None
                or task.run_attempts != generation
                or not self._is_after(task.lease_expires_at, now)
            ):
                return False
        elif generation is not None:
            return False
        self._set_terminal(task, now)
        await self.session.flush()
        return True

    async def cancel(self, task_token: TaskToken) -> bool:
        task = await self.get_by_token(task_token, for_update=True)
        if task is None:
            return False
        if task.state is ImportTaskState.CANCELLED:
            return True
        if task.state not in {ImportTaskState.REQUESTED, ImportTaskState.RETRY_WAIT}:
            return False
        now = await self._current_time()
        task.state = ImportTaskState.CANCELLED
        task.completed_at = now
        task.next_retry_at = None
        task.lease_expires_at = None
        await self.session.flush()
        return True

    async def reconcile(self, *, limit: int | None = None) -> ReconcileDecision:
        configured_limit = self.settings.import_task_reconcile_batch_size
        if limit is not None and limit < 1:
            raise ValueError("Reconciliation limit must be positive")
        batch_limit = configured_limit if limit is None else min(configured_limit, limit)
        now = await self._current_time()
        tasks = await self.repository.select_reconcilable(now=now, limit=batch_limit)
        dispatches: list[TaskEnvelope] = []
        terminal: list[TaskEnvelope] = []
        deferred: list[TaskEnvelope] = []

        for task in tasks:
            envelope = TaskEnvelope.from_task(task)
            if task.state is ImportTaskState.RUNNING:
                if task.run_attempts >= self._max_run_attempts(task.task_kind):
                    self._set_terminal(task, now)
                    terminal.append(envelope)
                else:
                    retry_at = now + timedelta(seconds=self._run_backoff(task.run_attempts))
                    self._set_retry_wait(task, retry_at)
                    deferred.append(envelope)
                continue

            decision = self._reserve_locked(task, now)
            if decision.status is DispatchStatus.RESERVED:
                assert decision.envelope is not None
                dispatches.append(decision.envelope)
            elif decision.status is DispatchStatus.EXHAUSTED:
                terminal.append(envelope)

        await self.session.flush()
        return ReconcileDecision(
            dispatches=tuple(dispatches),
            terminal=tuple(terminal),
            deferred=tuple(deferred),
        )

    def _reserve_locked(self, task: ImportTaskRequest, now: datetime) -> DispatchDecision:
        if task.state is ImportTaskState.COMPLETED:
            return DispatchDecision(DispatchStatus.COMPLETED, task)
        if task.state is ImportTaskState.RUNNING:
            return DispatchDecision(DispatchStatus.BUSY, task)
        if task.state in {ImportTaskState.TERMINAL_FAILED, ImportTaskState.CANCELLED}:
            return DispatchDecision(DispatchStatus.INACTIVE, task)
        if self._is_after(task.next_retry_at, now):
            return DispatchDecision(DispatchStatus.NOT_DUE, task)
        if task.dispatch_attempts >= self.settings.import_task_dispatch_max_attempts:
            self._set_terminal(task, now)
            return DispatchDecision(DispatchStatus.EXHAUSTED, task)

        task.state = ImportTaskState.REQUESTED
        task.dispatch_attempts += 1
        task.last_dispatch_attempt_at = now
        task.next_retry_at = now + timedelta(seconds=self._dispatch_backoff(task.dispatch_attempts))
        task.lease_expires_at = None
        envelope = TaskEnvelope.from_task(task)
        return DispatchDecision(DispatchStatus.RESERVED, task, envelope)

    def _set_retry_wait(self, task: ImportTaskRequest, retry_at: datetime) -> None:
        task.state = ImportTaskState.RETRY_WAIT
        task.next_retry_at = retry_at
        task.lease_expires_at = None
        task.completed_at = None

    @staticmethod
    def _set_terminal(task: ImportTaskRequest, now: datetime) -> None:
        task.state = ImportTaskState.TERMINAL_FAILED
        task.completed_at = now
        task.next_retry_at = None
        task.lease_expires_at = None

    @staticmethod
    def _matches(task: ImportTaskRequest, envelope: TaskEnvelope) -> bool:
        return (
            task.task_token == envelope.task_token
            and task.task_kind == envelope.task_kind
            and task.import_job_id == envelope.import_job_id
            and task.import_job_file_id == envelope.import_job_file_id
            and task.preview_revision == envelope.preview_revision
        )

    @staticmethod
    def _validate_target(
        *,
        task_kind: ImportTaskKind,
        import_job_file_id: UUID | None,
        preview_revision: int | None,
    ) -> None:
        if task_kind is ImportTaskKind.FILE_PARSE:
            if import_job_file_id is None or preview_revision is not None:
                raise ValueError("file_parse requires a file target and no preview revision")
            return
        if task_kind is ImportTaskKind.CONFIRM:
            if import_job_file_id is not None or preview_revision is None or preview_revision < 1:
                raise ValueError("confirm requires a positive preview revision and no file target")
            return
        if import_job_file_id is not None or preview_revision is not None:
            raise ValueError(f"{task_kind.value} is a job-level target without a revision")

    def _max_run_attempts(self, task_kind: ImportTaskKind) -> int:
        return {
            ImportTaskKind.LEGACY_PARSE: self.settings.import_task_legacy_parse_max_run_attempts,
            ImportTaskKind.FILE_PARSE: self.settings.import_task_file_parse_max_run_attempts,
            ImportTaskKind.PREVIEW: self.settings.import_task_preview_max_run_attempts,
            ImportTaskKind.CONFIRM: self.settings.import_task_confirm_max_run_attempts,
        }[task_kind]

    def _dispatch_backoff(self, attempt: int) -> int:
        return _bounded_exponential_backoff(
            self.settings.import_task_dispatch_backoff_base_seconds,
            self.settings.import_task_dispatch_backoff_max_seconds,
            attempt,
        )

    def _run_backoff(self, attempt: int) -> int:
        return _bounded_exponential_backoff(
            self.settings.import_task_run_backoff_base_seconds,
            self.settings.import_task_run_backoff_max_seconds,
            attempt,
        )

    async def _current_time(self) -> datetime:
        if self.clock is not None:
            now = self.clock()
        elif self._uses_postgres_clock():
            value = await self.session.scalar(select(func.clock_timestamp()))
            if not isinstance(value, datetime):  # pragma: no cover - driver invariant
                raise RuntimeError("PostgreSQL did not return clock_timestamp()")
            now = value
        else:
            now = utc_now()
        if now.tzinfo is None:
            raise ValueError("Import task clock must return a timezone-aware datetime")
        return now.astimezone(UTC)

    def _uses_postgres_clock(self) -> bool:
        return (
            self.clock is None
            and self.session.bind is not None
            and self.session.bind.dialect.name == "postgresql"
        )

    @staticmethod
    def _is_after(value: datetime | None, boundary: datetime) -> bool:
        if value is None:
            return False
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value > boundary


def _bounded_exponential_backoff(base: int, maximum: int, attempt: int) -> int:
    exponent = max(0, min(attempt - 1, 30))
    return min(maximum, base * (1 << exponent))


def _optional_uuid(value: TaskToken) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError):
        return None


def _require_uuid(value: UUID | str, field: str) -> UUID:
    parsed = _optional_uuid(value)
    if parsed is None:
        raise ValueError(f"{field} must be a UUID")
    return parsed
