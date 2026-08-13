"""Celery delivery for PostgreSQL-authoritative durable import tasks."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.repository import AuditRepository
from backend_core.config import Settings, get_settings
from backend_core.db import Database
from backend_core.imports.batch_processor import BatchImportProcessor
from backend_core.imports.confirm_processor import BulkConfirmProcessor
from backend_core.imports.enums import (
    ImportJobFailedStage,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportTaskKind,
    ImportTaskState,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.hashing import advisory_lock_key
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.processor import ImportProcessor
from backend_core.imports.repository import ImportRepository
from backend_core.imports.storage import LocalStorageAdapter
from backend_core.imports.task_service import (
    ClaimDecision,
    ClaimStatus,
    ImportTaskService,
    ReconcileDecision,
    RetryStatus,
    TaskEnvelope,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app

LOGGER = logging.getLogger(__name__)
HEAVY_IMPORT_LOCK_KEY = advisory_lock_key("phase2:heavy-import")
# Compatibility alias retained for Task 5 instrumentation and deployments.
HEAVY_IMPORT_PREVIEW_LOCK_KEY = HEAVY_IMPORT_LOCK_KEY

_RETRYABLE_DOMAIN_CODES = frozenset(
    {
        "IMPORT_PROCESSING_FAILED",
        "IMPORT_PREVIEW_FAILED",
        "IMPORT_CONFIRM_FAILED",
    }
)


@dataclass(frozen=True, slots=True)
class ReconcileRun:
    selected: int
    published: int
    publish_failed: int
    terminal: int
    deferred: int


def _parser_limits(settings: Settings) -> ParserLimits:
    return ParserLimits(
        max_xlsx_uncompressed_bytes=settings.import_max_xlsx_uncompressed_bytes,
        max_xlsx_entries=settings.import_max_xlsx_entries,
        max_xlsx_compression_ratio=settings.import_max_xlsx_compression_ratio,
        max_rows=settings.import_max_rows,
        max_columns=settings.import_max_columns,
        max_cells=settings.import_max_cells,
        max_cell_chars=settings.import_max_cell_chars,
        max_warnings=settings.import_max_warnings,
    )


def _envelope(
    task_kind: ImportTaskKind,
    import_job_id: UUID | str,
    task_token: UUID | str,
    *,
    import_job_file_id: UUID | str | None = None,
    preview_revision: int | None = None,
) -> TaskEnvelope:
    return TaskEnvelope.from_payload(
        task_token=task_token,
        task_kind=task_kind,
        import_job_id=import_job_id,
        import_job_file_id=import_job_file_id,
        preview_revision=preview_revision,
    )


async def _claim(
    session: AsyncSession,
    settings: Settings,
    envelope: TaskEnvelope,
) -> ClaimDecision:
    service = ImportTaskService(session, settings)
    decision = await service.claim(envelope)
    if decision.status is ClaimStatus.EXHAUSTED:
        await _sync_terminal_business(session, envelope, reason="run_attempts_exhausted")
    await session.commit()
    return decision


async def _sync_terminal_business(
    session: AsyncSession,
    envelope: TaskEnvelope,
    *,
    reason: str,
) -> None:
    """Project retry exhaustion to the still-current Job/File in the same transaction."""

    repository = ImportRepository(session)
    # Task transitions lock the request row first, while API/processor flows
    # intentionally lock Job -> task.  NOWAIT turns a possible lock inversion
    # into a full transaction rollback and later redelivery/reconciliation,
    # rather than allowing PostgreSQL to form a deadlock cycle.  The task and
    # business failure projection still commit atomically once the Job is free.
    job = await repository.get_import_job(
        envelope.import_job_id,
        for_update=True,
        nowait=True,
    )
    if job is None:
        return
    token = str(envelope.task_token)
    terminal_details = {
        "deterministic_validation_failed": (
            "IMPORT_TASK_DETERMINISTIC_FAILED",
            "Import task failed deterministic validation",
        ),
        "processor_returned_unsettled": (
            "IMPORT_TASK_INVARIANT_FAILED",
            "Import processor ended without settling its durable task",
        ),
    }
    code, message = terminal_details.get(
        reason,
        (
            "IMPORT_TASK_RETRY_EXHAUSTED",
            "Import task exhausted its persisted retry policy",
        ),
    )

    if envelope.task_kind is ImportTaskKind.FILE_PARSE:
        if envelope.import_job_file_id is None:
            return
        occurrence = await repository.get_import_job_file(
            envelope.import_job_file_id, for_update=True
        )
        if (
            occurrence is not None
            and occurrence.import_job_id == job.id
            and occurrence.parse_task_id == token
            and occurrence.status
            in {
                ImportJobFileStatus.UPLOADED,
                ImportJobFileStatus.PARSING,
                ImportJobFileStatus.FAILED,
            }
        ):
            occurrence.status = ImportJobFileStatus.FAILED
            occurrence.parse_completed_at = datetime.now(UTC)
            occurrence.error_code = code
            occurrence.error_message = message
            AuditRepository(session).add(
                action=AuditAction.IMPORT_FILE_RETRIED,
                result=AuditResult.FAILED,
                department_id=job.department_id,
                operator_id=job.operator_id,
                ip="worker",
                user_agent="imports.durable_task",
                entity_type="import_job_file",
                entity_id=occurrence.id,
                after={
                    "task_token": token,
                    "task_kind": envelope.task_kind.value,
                    "reason": reason,
                },
            )
        return

    if envelope.task_kind is ImportTaskKind.LEGACY_PARSE:
        if job.parse_task_id != token or job.status in {
            ImportJobStatus.COMPLETED,
            ImportJobStatus.CANCELLED,
        }:
            return
        occurrences = await repository.list_import_job_files(job.id, for_update=True)
        if len(occurrences) == 1:
            occurrences[0].status = ImportJobFileStatus.FAILED
            occurrences[0].parse_completed_at = datetime.now(UTC)
            occurrences[0].error_code = code
            occurrences[0].error_message = message
        job.status = ImportJobStatus.FAILED
        job.failed_stage = ImportJobFailedStage.PREVIEW
    elif envelope.task_kind is ImportTaskKind.PREVIEW:
        if job.parse_task_id != token or job.status not in {
            ImportJobStatus.PREVIEWING,
            ImportJobStatus.FAILED,
        }:
            return
        job.status = ImportJobStatus.FAILED
        job.failed_stage = ImportJobFailedStage.PREVIEW
    elif envelope.task_kind is ImportTaskKind.CONFIRM:
        if (
            job.confirm_task_id != token
            or job.confirmed_revision != envelope.preview_revision
            or job.status
            not in {
                ImportJobStatus.CONFIRM_QUEUED,
                ImportJobStatus.IMPORTING,
                ImportJobStatus.FAILED,
            }
        ):
            return
        job.status = ImportJobStatus.FAILED
        job.failed_stage = ImportJobFailedStage.CONFIRM
    else:  # pragma: no cover - exhaustive enum guard
        return
    job.error_code = code
    job.error_message = message
    AuditRepository(session).add(
        action=(
            AuditAction.IMPORT_FAILED
            if envelope.task_kind is ImportTaskKind.LEGACY_PARSE
            else AuditAction.IMPORT_BATCH_FAILED
        ),
        result=AuditResult.FAILED,
        department_id=job.department_id,
        operator_id=job.operator_id,
        ip="worker",
        user_agent="imports.durable_task",
        entity_type="import_job",
        entity_id=job.id,
        after={
            "task_token": token,
            "task_kind": envelope.task_kind.value,
            "reason": reason,
        },
    )


async def _add_retry_audit(
    session: AsyncSession,
    envelope: TaskEnvelope,
    *,
    reason: str,
    run_attempt: int,
    dispatch_attempt: int,
) -> None:
    """Record a whitelisted operational event; never use it for recovery."""

    repository = ImportRepository(session)
    job = await repository.get_import_job(envelope.import_job_id)
    if job is None:
        return
    entity_type = "import_job"
    entity_id = job.id
    action = AuditAction.IMPORT_BATCH_RETRIED
    if envelope.task_kind is ImportTaskKind.FILE_PARSE:
        entity_type = "import_job_file"
        entity_id = envelope.import_job_file_id or job.id
        action = AuditAction.IMPORT_FILE_RETRIED
    AuditRepository(session).add(
        action=action,
        result=AuditResult.SUCCESS,
        department_id=job.department_id,
        operator_id=job.operator_id,
        ip="worker",
        user_agent="imports.reconcile_import_tasks",
        entity_type=entity_type,
        entity_id=entity_id,
        after={
            "task_token": str(envelope.task_token),
            "task_kind": envelope.task_kind.value,
            "run_attempt": run_attempt,
            "dispatch_attempt": dispatch_attempt,
            "reason": reason,
        },
    )


async def _terminalize(
    database: Database,
    settings: Settings,
    envelope: TaskEnvelope,
    *,
    generation: int,
) -> None:
    async with database.session_factory() as session:
        service = ImportTaskService(session, settings)
        changed = await service.terminal_fail(
            envelope.task_token,
            generation=generation,
        )
        if changed:
            await _sync_terminal_business(
                session,
                envelope,
                reason="deterministic_validation_failed",
            )
        await session.commit()


async def _record_transient_failure(
    database: Database,
    settings: Settings,
    envelope: TaskEnvelope,
    *,
    generation: int,
) -> RetryStatus:
    """Persist one bounded retry decision; never consult Celery request metadata."""

    async with database.session_factory() as session:
        service = ImportTaskService(session, settings)
        decision = await service.schedule_retry(envelope.task_token, generation)
        if decision.status is RetryStatus.EXHAUSTED:
            await _sync_terminal_business(session, envelope, reason="run_attempts_exhausted")
        elif decision.status is RetryStatus.SCHEDULED and decision.task is not None:
            await _add_retry_audit(
                session,
                envelope,
                reason="transient_worker_failure",
                run_attempt=decision.task.run_attempts,
                dispatch_attempt=decision.task.dispatch_attempts,
            )
        await session.commit()
        return decision.status


async def _heartbeat_thread_loop(
    settings: Settings,
    envelope: TaskEnvelope,
    generation: int,
    stopped: threading.Event,
) -> None:
    """Renew from an independent event loop so synchronous parsing cannot starve it."""

    database: Database | None = None
    try:
        while not stopped.wait(settings.import_task_heartbeat_seconds):
            try:
                database = database or Database(settings.database_url)
                async with database.session_factory() as session:
                    renewed = await ImportTaskService(session, settings).heartbeat(
                        envelope.task_token,
                        generation,
                    )
                    await session.commit()
                if not renewed:
                    return
            except Exception:
                # A temporary heartbeat connection failure cannot authorize
                # work; the final generation+lease guard rejects a late commit.
                LOGGER.warning("durable import task heartbeat failed")
    finally:
        if database is not None:
            await database.close()


def _heartbeat_thread_entry(
    settings: Settings,
    envelope: TaskEnvelope,
    generation: int,
    stopped: threading.Event,
) -> None:
    try:
        asyncio.run(_heartbeat_thread_loop(settings, envelope, generation, stopped))
    except BaseException:
        # Never let a driver traceback from the lease helper escape the thread;
        # the processor's final PostgreSQL lease CAS remains authoritative.
        LOGGER.warning("durable import task heartbeat stopped unexpectedly")


async def _with_heartbeat(
    database: Database,
    settings: Settings,
    envelope: TaskEnvelope,
    generation: int,
    operation: Callable[[], Awaitable[object]],
) -> None:
    # ``operation`` includes synchronous storage reads, XLSX/CSV parsing and
    # in-memory planning.  A coroutine on this same event loop could be starved
    # for an entire lease, so renewal owns a separate thread, loop and engine.
    del database
    stopped = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_thread_entry,
        args=(settings, envelope, generation, stopped),
        name="durable-import-heartbeat",
        daemon=True,
    )
    heartbeat.start()
    try:
        await operation()
    finally:
        stopped.set()
        await asyncio.to_thread(heartbeat.join, 5.0)
        if heartbeat.is_alive():
            LOGGER.warning("durable import task heartbeat did not stop promptly")


def _is_deterministic(error: BaseException) -> bool:
    return isinstance(error, ImportDomainError) and error.code not in _RETRYABLE_DOMAIN_CODES


async def _handle_execution_error(
    database: Database,
    settings: Settings,
    envelope: TaskEnvelope,
    generation: int,
    error: BaseException,
) -> None:
    if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
        raise error
    if _is_deterministic(error):
        await _terminalize(database, settings, envelope, generation=generation)
        return
    # Commit-result uncertainty converges here: schedule_retry first probes the
    # authoritative row and returns COMPLETED if the business+task commit won.
    await _record_transient_failure(
        database,
        settings,
        envelope,
        generation=generation,
    )


async def _recover_execution_error(
    session: AsyncSession,
    database: Database,
    settings: Settings,
    envelope: TaskEnvelope,
    generation: int,
    error: BaseException,
) -> None:
    """Sanitize a second failure while PostgreSQL recovery is being persisted."""

    try:
        # Rollback can itself fail after a lost database connection. Keep it in
        # the same sanitizing boundary as the durable retry/terminal projection
        # so the active parser/storage exception context never reaches Celery.
        await session.rollback()
        await _handle_execution_error(database, settings, envelope, generation, error)
    except BaseException as recovery_error:
        if isinstance(recovery_error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise recovery_error from None
        # The original parser/driver exception may carry raw bytes or SQL bound
        # values.  Suppress both nested exception contexts at the Celery edge;
        # lease expiry and the reconciler still recover the PostgreSQL request.
        raise ImportDomainError(
            "IMPORT_TASK_RECOVERY_DEFERRED",
            "Import task recovery was deferred to the persisted reconciler",
        ) from None


async def _verify_success_settled(
    database: Database,
    settings: Settings,
    envelope: TaskEnvelope,
    generation: int,
) -> None:
    """Ensure a successful processor return did not leave its claimed task live."""

    async with database.session_factory() as session:
        service = ImportTaskService(session, settings)
        task = await service.get_by_token(envelope.task_token, for_update=True)
        if task is None or task.state in {
            ImportTaskState.COMPLETED,
            ImportTaskState.TERMINAL_FAILED,
            ImportTaskState.CANCELLED,
        }:
            await session.rollback()
            return
        if (
            task.state is ImportTaskState.RUNNING
            and task.run_attempts == generation
            and await service.terminal_fail(envelope.task_token, generation=generation)
        ):
            # This is an invariant failure (normally an idempotent stale business
            # delivery).  Never leave a claimed lease hanging indefinitely.
            await _sync_terminal_business(
                session,
                envelope,
                reason="processor_returned_unsettled",
            )
            await session.commit()
            LOGGER.error("import processor returned without settling its durable task")
            return
        await session.rollback()


async def _run_nonheavy(
    envelope: TaskEnvelope,
    runner: Callable[[AsyncSession, Settings, int], Awaitable[object]],
) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session:
            claim = await _claim(session, settings, envelope)
            if claim.status is not ClaimStatus.CLAIMED:
                return
            assert claim.generation is not None

            async def operation() -> object:
                return await runner(session, settings, claim.generation or 0)

            try:
                await _with_heartbeat(
                    database,
                    settings,
                    envelope,
                    claim.generation,
                    operation,
                )
                await _verify_success_settled(
                    database,
                    settings,
                    envelope,
                    claim.generation,
                )
            except BaseException as error:
                await _recover_execution_error(
                    session,
                    database,
                    settings,
                    envelope,
                    claim.generation,
                    error,
                )
    finally:
        await database.close()


async def _run_heavy(
    envelope: TaskEnvelope,
    runner: Callable[[AsyncSession, Settings, int], Awaitable[object]],
) -> bool:
    """Acquire the global heavy gate before claim, so gate contention costs no run."""

    settings = get_settings()
    database = Database(settings.database_url)
    acquired = False
    try:
        # This is a session-scoped PostgreSQL advisory lock; both unlock and all
        # heavy SQL remain pinned to the same physical connection.
        async with database.engine.connect() as connection:
            acquired = bool(
                await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": HEAVY_IMPORT_LOCK_KEY},
                )
            )
            await connection.commit()
            if not acquired:
                return False
            try:
                async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                    claim = await _claim(session, settings, envelope)
                    if claim.status is not ClaimStatus.CLAIMED:
                        return True
                    assert claim.generation is not None

                    async def operation() -> object:
                        return await runner(session, settings, claim.generation or 0)

                    try:
                        await _with_heartbeat(
                            database,
                            settings,
                            envelope,
                            claim.generation,
                            operation,
                        )
                        await _verify_success_settled(
                            database,
                            settings,
                            envelope,
                            claim.generation,
                        )
                    except BaseException as error:
                        await _recover_execution_error(
                            session,
                            database,
                            settings,
                            envelope,
                            claim.generation,
                            error,
                        )
                return True
            finally:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": HEAVY_IMPORT_LOCK_KEY},
                )
                await connection.commit()
    finally:
        await database.close()


async def _parse(import_job_id: UUID, task_token: UUID | str) -> None:
    envelope = _envelope(ImportTaskKind.LEGACY_PARSE, import_job_id, task_token)

    async def run(session: AsyncSession, settings: Settings, generation: int) -> object:
        processor = ImportProcessor(
            session,
            LocalStorageAdapter(settings.import_data_dir),
            parser_limits=_parser_limits(settings),
            task_settings=settings,
        )
        return await processor.parse_and_preview(
            import_job_id,
            task_token=envelope.task_token,
            task_generation=generation,
        )

    await _run_nonheavy(envelope, run)


async def _parse_file(
    import_job_id: UUID,
    import_job_file_id: UUID,
    task_token: UUID | str,
) -> None:
    envelope = _envelope(
        ImportTaskKind.FILE_PARSE,
        import_job_id,
        task_token,
        import_job_file_id=import_job_file_id,
    )

    async def run(session: AsyncSession, settings: Settings, generation: int) -> object:
        processor = BatchImportProcessor(
            session,
            LocalStorageAdapter(settings.import_data_dir),
            parser_limits=_parser_limits(settings),
            max_batch_rows=settings.import_max_batch_rows,
            task_settings=settings,
        )
        return await processor.parse_file(
            import_job_id,
            import_job_file_id,
            str(envelope.task_token),
            task_token=envelope.task_token,
            task_generation=generation,
        )

    await _run_nonheavy(envelope, run)


async def _preview(import_job_id: UUID, task_token: UUID | str) -> bool:
    envelope = _envelope(ImportTaskKind.PREVIEW, import_job_id, task_token)

    async def run(session: AsyncSession, settings: Settings, generation: int) -> object:
        from backend_core.imports.preview_processor import UnifiedPreviewProcessor

        processor = UnifiedPreviewProcessor(
            session,
            LocalStorageAdapter(settings.import_data_dir),
            parser_limits=_parser_limits(settings),
            max_batch_rows=settings.import_max_batch_rows,
            task_settings=settings,
        )
        return await processor.build(
            import_job_id,
            str(envelope.task_token),
            task_token=envelope.task_token,
            task_generation=generation,
        )

    return await _run_heavy(envelope, run)


async def _confirm(
    import_job_id: UUID,
    preview_revision: int,
    task_token: UUID | str,
) -> bool:
    envelope = _envelope(
        ImportTaskKind.CONFIRM,
        import_job_id,
        task_token,
        preview_revision=preview_revision,
    )

    async def run(session: AsyncSession, settings: Settings, generation: int) -> object:
        job = await ImportRepository(session).get_import_job(import_job_id)
        if job is None:
            raise ImportDomainError("IMPORT_JOB_NOT_FOUND", "Import job not found", status_code=404)
        if job.stored_file_id is None:
            bulk_processor = BulkConfirmProcessor(
                session,
                LocalStorageAdapter(settings.import_data_dir),
                parser_limits=_parser_limits(settings),
                settings=settings,
                max_batch_rows=settings.import_max_batch_rows,
            )
            return await bulk_processor.confirm(
                import_job_id,
                preview_revision,
                envelope.task_token,
                generation,
            )
        legacy_processor = ImportProcessor(
            session,
            LocalStorageAdapter(settings.import_data_dir),
            parser_limits=_parser_limits(settings),
            task_settings=settings,
        )
        return await legacy_processor.confirm(
            import_job_id,
            preview_revision,
            task_token=envelope.task_token,
            task_generation=generation,
        )

    return await _run_heavy(envelope, run)


def _publish_envelope(envelope: TaskEnvelope) -> None:
    task_name = {
        ImportTaskKind.LEGACY_PARSE: "imports.parse_import_job",
        ImportTaskKind.FILE_PARSE: "imports.parse_import_job_file",
        ImportTaskKind.PREVIEW: "imports.preview_import_job",
        ImportTaskKind.CONFIRM: "imports.confirm_import_job",
    }[envelope.task_kind]
    kwargs = envelope.broker_kwargs()
    celery_app.send_task(
        task_name,
        kwargs=kwargs,
        task_id=str(envelope.task_token),
        queue="import",
        retry=False,
    )


async def _reconcile(
    publisher: Callable[[TaskEnvelope], None] = _publish_envelope,
) -> ReconcileRun:
    settings = get_settings()
    database = Database(settings.database_url)
    decision: ReconcileDecision
    try:
        async with database.session_factory() as session:
            service = ImportTaskService(session, settings)
            decision = await service.reconcile()
            for reconciled in decision.dispatches:
                task = await service.get_by_token(reconciled.task_token)
                if task is not None:
                    await _add_retry_audit(
                        session,
                        reconciled,
                        reason="reconciliation_dispatch",
                        run_attempt=task.run_attempts,
                        dispatch_attempt=task.dispatch_attempts,
                    )
            for expired in decision.deferred:
                task = await service.get_by_token(expired.task_token)
                if task is not None:
                    await _add_retry_audit(
                        session,
                        expired,
                        reason="expired_worker_lease",
                        run_attempt=task.run_attempts,
                        dispatch_attempt=task.dispatch_attempts,
                    )
            for terminal in decision.terminal:
                await _sync_terminal_business(
                    session,
                    terminal,
                    reason="persisted_attempts_exhausted",
                )
            # Reservation/counters are authoritative before any broker call.
            await session.commit()

        published = 0
        publish_failed = 0
        for envelope in decision.dispatches:
            try:
                await asyncio.to_thread(publisher, envelope)
                published += 1
            except Exception:
                # The committed visibility timeout makes this request due again;
                # the same token may be published twice and claim stays idempotent.
                publish_failed += 1
                LOGGER.warning("durable import task dispatch failed")
        return ReconcileRun(
            selected=(len(decision.dispatches) + len(decision.terminal) + len(decision.deferred)),
            published=published,
            publish_failed=publish_failed,
            terminal=len(decision.terminal),
            deferred=len(decision.deferred),
        )
    finally:
        await database.close()


@celery_app.task(
    name="imports.parse_import_job",
    bind=False,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)  # type: ignore[untyped-decorator]
def parse_import_job(import_job_id: str, task_token: str) -> None:
    asyncio.run(_parse(UUID(import_job_id), UUID(task_token)))


@celery_app.task(
    name="imports.parse_import_job_file",
    bind=False,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)  # type: ignore[untyped-decorator]
def parse_import_job_file(
    import_job_id: str,
    import_job_file_id: str,
    task_token: str,
) -> None:
    asyncio.run(
        _parse_file(
            UUID(import_job_id),
            UUID(import_job_file_id),
            UUID(task_token),
        )
    )


@celery_app.task(
    name="imports.preview_import_job",
    bind=False,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)  # type: ignore[untyped-decorator]
def preview_import_job(import_job_id: str, task_token: str) -> None:
    asyncio.run(_preview(UUID(import_job_id), UUID(task_token)))


@celery_app.task(
    name="imports.confirm_import_job",
    bind=False,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)  # type: ignore[untyped-decorator]
def confirm_import_job(import_job_id: str, preview_revision: int, task_token: str) -> None:
    asyncio.run(_confirm(UUID(import_job_id), preview_revision, UUID(task_token)))


@celery_app.task(
    name="imports.reconcile_import_tasks",
    bind=False,
    ignore_result=True,
)  # type: ignore[untyped-decorator]
def reconcile_import_tasks() -> None:
    asyncio.run(_reconcile())


__all__ = [
    "confirm_import_job",
    "parse_import_job",
    "parse_import_job_file",
    "preview_import_job",
    "reconcile_import_tasks",
]
