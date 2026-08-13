"""Celery delivery contracts for PostgreSQL-authoritative import tasks."""

import asyncio
import logging
import traceback
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from app.celery_app import celery_app
from app.tasks import imports as import_tasks
from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.config import Settings, get_settings
from backend_core.imports.enums import ImportJobStatus, ImportTaskKind
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.task_service import (
    ClaimDecision,
    ClaimStatus,
    ReconcileDecision,
    RetryStatus,
    TaskEnvelope,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        import_task_heartbeat_seconds=1,
        import_task_lease_seconds=10,
    )


def _async_context(value: object) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=value)
    context.__aexit__ = AsyncMock(return_value=None)
    return context


def _envelope(kind: ImportTaskKind = ImportTaskKind.PREVIEW) -> TaskEnvelope:
    return TaskEnvelope(
        task_token=uuid4(),
        task_kind=kind,
        import_job_id=uuid4(),
        import_job_file_id=uuid4() if kind is ImportTaskKind.FILE_PARSE else None,
        preview_revision=4 if kind is ImportTaskKind.CONFIRM else None,
    )


def test_import_tasks_and_reconciler_routes_are_registered() -> None:
    for name in (
        "imports.parse_import_job",
        "imports.parse_import_job_file",
        "imports.preview_import_job",
        "imports.confirm_import_job",
    ):
        task = celery_app.tasks[name]
        assert task.ignore_result is True
        assert task.acks_late is True
        assert task.reject_on_worker_lost is True
        assert celery_app.conf.task_routes[name] == {"queue": "import"}

    reconciler = celery_app.tasks["imports.reconcile_import_tasks"]
    assert reconciler.ignore_result is True
    assert celery_app.conf.task_routes["imports.reconcile_import_tasks"] == {"queue": "default"}
    schedule = celery_app.conf.beat_schedule["reconcile-durable-import-tasks"]
    assert schedule["task"] == "imports.reconcile_import_tasks"
    assert schedule["schedule"] == get_settings().import_task_reconcile_interval_seconds
    assert schedule["options"] == {"queue": "default"}
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_task_entrypoints_validate_and_delegate_id_only_payloads() -> None:
    job_id = uuid4()
    file_id = uuid4()
    token = uuid4()

    with patch.object(import_tasks, "_parse", AsyncMock()) as delegate:
        import_tasks.parse_import_job.run(str(job_id), str(token))
        delegate.assert_awaited_once_with(job_id, token)

    with patch.object(import_tasks, "_parse_file", AsyncMock()) as delegate:
        import_tasks.parse_import_job_file.run(str(job_id), str(file_id), str(token))
        delegate.assert_awaited_once_with(job_id, file_id, token)

    with patch.object(import_tasks, "_preview", AsyncMock(return_value=True)) as delegate:
        import_tasks.preview_import_job.run(str(job_id), str(token))
        delegate.assert_awaited_once_with(job_id, token)

    with patch.object(import_tasks, "_confirm", AsyncMock(return_value=True)) as delegate:
        import_tasks.confirm_import_job.run(str(job_id), 7, str(token))
        delegate.assert_awaited_once_with(job_id, 7, token)


@pytest.mark.parametrize(
    ("call"),
    [
        lambda: import_tasks.parse_import_job.run("bad", str(uuid4())),
        lambda: import_tasks.parse_import_job.run(str(uuid4()), "bad"),
        lambda: import_tasks.parse_import_job_file.run("bad", str(uuid4()), str(uuid4())),
        lambda: import_tasks.parse_import_job_file.run(str(uuid4()), "bad", str(uuid4())),
        lambda: import_tasks.preview_import_job.run(str(uuid4()), "bad"),
        lambda: import_tasks.confirm_import_job.run(str(uuid4()), 1, "bad"),
    ],
)
def test_invalid_broker_identifiers_are_rejected_before_database_work(call: object) -> None:
    with pytest.raises(ValueError):
        call()  # type: ignore[operator]


def test_publish_maps_each_kind_to_an_id_only_message_with_same_token() -> None:
    for kind, name in (
        (ImportTaskKind.LEGACY_PARSE, "imports.parse_import_job"),
        (ImportTaskKind.FILE_PARSE, "imports.parse_import_job_file"),
        (ImportTaskKind.PREVIEW, "imports.preview_import_job"),
        (ImportTaskKind.CONFIRM, "imports.confirm_import_job"),
    ):
        envelope = _envelope(kind)
        with patch.object(celery_app, "send_task") as send:
            import_tasks._publish_envelope(envelope)
        send.assert_called_once_with(
            name,
            kwargs=envelope.broker_kwargs(),
            task_id=str(envelope.task_token),
            queue="import",
            retry=False,
        )
        payload = send.call_args.kwargs["kwargs"]
        assert set(payload) <= {
            "task_token",
            "import_job_id",
            "import_job_file_id",
            "preview_revision",
        }


def test_heavy_gate_is_checked_before_claim_and_busy_costs_no_run_attempt() -> None:
    envelope = _envelope()
    connection = MagicMock()
    connection.scalar = AsyncMock(return_value=False)
    connection.commit = AsyncMock(return_value=None)
    database = MagicMock()
    database.engine.connect.return_value = _async_context(connection)
    database.close = AsyncMock(return_value=None)
    claim = AsyncMock()
    runner = AsyncMock()

    with (
        patch.object(import_tasks, "get_settings", return_value=_settings()),
        patch.object(import_tasks, "Database", return_value=database),
        patch.object(import_tasks, "_claim", claim),
    ):
        assert asyncio.run(import_tasks._run_heavy(envelope, runner)) is False

    claim.assert_not_awaited()
    runner.assert_not_awaited()
    connection.execute.assert_not_called()
    database.close.assert_awaited_once_with()


def test_heavy_execution_claims_on_pinned_connection_and_unlocks() -> None:
    envelope = _envelope(ImportTaskKind.CONFIRM)
    connection = MagicMock()
    connection.scalar = AsyncMock(return_value=True)
    connection.commit = AsyncMock(return_value=None)
    connection.execute = AsyncMock(return_value=None)
    database = MagicMock()
    database.engine.connect.return_value = _async_context(connection)
    database.close = AsyncMock(return_value=None)
    session = MagicMock()
    session_context = _async_context(session)
    claim = ClaimDecision(ClaimStatus.CLAIMED, MagicMock(), generation=2)
    runner = AsyncMock(return_value={})

    with (
        patch.object(import_tasks, "get_settings", return_value=_settings()),
        patch.object(import_tasks, "Database", return_value=database),
        patch.object(import_tasks, "AsyncSession", return_value=session_context) as session_type,
        patch.object(import_tasks, "_claim", AsyncMock(return_value=claim)) as claim_task,
    ):
        assert asyncio.run(import_tasks._run_heavy(envelope, runner)) is True

    session_type.assert_called_once_with(bind=connection, expire_on_commit=False)
    claim_task.assert_awaited_once_with(session, _settings(), envelope)
    runner.assert_awaited_once_with(session, _settings(), 2)
    assert "pg_try_advisory_lock" in str(connection.scalar.await_args.args[0])
    assert "pg_advisory_unlock" in str(connection.execute.await_args.args[0])
    assert connection.scalar.await_args.args[1] == connection.execute.await_args.args[1]


def test_claim_commits_before_execution_and_terminal_exhaustion_is_projected() -> None:
    envelope = _envelope()
    session = MagicMock()
    session.commit = AsyncMock(return_value=None)
    service = MagicMock()
    service.claim = AsyncMock(
        return_value=ClaimDecision(ClaimStatus.EXHAUSTED, MagicMock(), generation=None)
    )

    with (
        patch.object(import_tasks, "ImportTaskService", return_value=service),
        patch.object(import_tasks, "_sync_terminal_business", AsyncMock()) as terminal,
    ):
        decision = asyncio.run(import_tasks._claim(session, _settings(), envelope))

    assert decision.status is ClaimStatus.EXHAUSTED
    terminal.assert_awaited_once_with(
        session,
        envelope,
        reason="run_attempts_exhausted",
    )
    session.commit.assert_awaited_once_with()


def test_failure_policy_uses_postgres_retry_and_not_celery_retry_metadata() -> None:
    database = MagicMock()
    settings = _settings()
    envelope = _envelope()
    terminal = AsyncMock()
    retry = AsyncMock(return_value=RetryStatus.SCHEDULED)

    with (
        patch.object(import_tasks, "_terminalize", terminal),
        patch.object(import_tasks, "_record_transient_failure", retry),
    ):
        asyncio.run(
            import_tasks._handle_execution_error(
                database,
                settings,
                envelope,
                1,
                ImportDomainError("PREVIEW_STALE", "Preview changed"),
            )
        )
        terminal.assert_awaited_once_with(database, settings, envelope, generation=1)
        retry.assert_not_awaited()

        terminal.reset_mock()
        asyncio.run(
            import_tasks._handle_execution_error(
                database,
                settings,
                envelope,
                1,
                RuntimeError("temporary worker failure"),
            )
        )
        retry.assert_awaited_once_with(database, settings, envelope, generation=1)
        terminal.assert_not_awaited()


def test_recovery_handler_secondary_failure_redacts_sensitive_original_chain_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "SENSITIVE_CONTACT_SENTINEL_must_never_escape"
    envelope = _envelope()
    settings = _settings()
    session = MagicMock()
    session.rollback = AsyncMock(return_value=None)
    database = MagicMock()
    database.session_factory.return_value = _async_context(session)
    database.close = AsyncMock(return_value=None)
    runner = AsyncMock(side_effect=RuntimeError(sentinel))

    async def execute_operation(
        _database: object,
        _settings_value: Settings,
        _envelope_value: TaskEnvelope,
        _generation: int,
        operation: object,
    ) -> None:
        await operation()  # type: ignore[operator]

    recovery_failure = RuntimeError("durable recovery handler failed safely")
    caplog.set_level(logging.DEBUG)
    with (
        patch.object(import_tasks, "get_settings", return_value=settings),
        patch.object(import_tasks, "Database", return_value=database),
        patch.object(
            import_tasks,
            "_claim",
            AsyncMock(
                return_value=ClaimDecision(
                    ClaimStatus.CLAIMED,
                    MagicMock(),
                    generation=1,
                )
            ),
        ),
        patch.object(
            import_tasks,
            "_with_heartbeat",
            AsyncMock(side_effect=execute_operation),
        ),
        patch.object(
            import_tasks,
            "_handle_execution_error",
            AsyncMock(side_effect=recovery_failure),
        ),
        pytest.raises(ImportDomainError, match="persisted reconciler") as raised,
    ):
        asyncio.run(import_tasks._run_nonheavy(envelope, runner))

    rendered_chain = "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert raised.value.code == "IMPORT_TASK_RECOVERY_DEFERRED"
    assert sentinel not in str(raised.value)
    assert sentinel not in rendered_chain
    assert sentinel not in rendered_logs
    session.rollback.assert_awaited_once_with()
    database.close.assert_awaited_once_with()


def test_rollback_secondary_failure_redacts_sensitive_original_chain_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "SENSITIVE_ROLLBACK_SENTINEL_must_never_escape"
    envelope = _envelope()
    settings = _settings()
    session = MagicMock()
    session.rollback = AsyncMock(side_effect=ConnectionError("rollback connection lost"))
    database = MagicMock()
    database.session_factory.return_value = _async_context(session)
    database.close = AsyncMock(return_value=None)
    runner = AsyncMock(side_effect=RuntimeError(sentinel))

    async def execute_operation(
        _database: object,
        _settings_value: Settings,
        _envelope_value: TaskEnvelope,
        _generation: int,
        operation: object,
    ) -> None:
        await operation()  # type: ignore[operator]

    caplog.set_level(logging.DEBUG)
    with (
        patch.object(import_tasks, "get_settings", return_value=settings),
        patch.object(import_tasks, "Database", return_value=database),
        patch.object(
            import_tasks,
            "_claim",
            AsyncMock(
                return_value=ClaimDecision(
                    ClaimStatus.CLAIMED,
                    MagicMock(),
                    generation=1,
                )
            ),
        ),
        patch.object(
            import_tasks,
            "_with_heartbeat",
            AsyncMock(side_effect=execute_operation),
        ),
        patch.object(import_tasks, "_handle_execution_error", AsyncMock()) as recovery,
        pytest.raises(ImportDomainError, match="persisted reconciler") as raised,
    ):
        asyncio.run(import_tasks._run_nonheavy(envelope, runner))

    rendered_chain = "".join(
        traceback.format_exception(
            type(raised.value),
            raised.value,
            raised.value.__traceback__,
        )
    )
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert raised.value.code == "IMPORT_TASK_RECOVERY_DEFERRED"
    assert sentinel not in str(raised.value)
    assert sentinel not in rendered_chain
    assert sentinel not in rendered_logs
    session.rollback.assert_awaited_once_with()
    recovery.assert_not_awaited()
    database.close.assert_awaited_once_with()


def test_reconciler_commits_reservations_before_publish_and_retains_publish_failure() -> None:
    first = _envelope(ImportTaskKind.PREVIEW)
    second = _envelope(ImportTaskKind.CONFIRM)
    terminal = _envelope(ImportTaskKind.FILE_PARSE)
    deferred = _envelope(ImportTaskKind.LEGACY_PARSE)
    decision = ReconcileDecision(
        dispatches=(first, second),
        terminal=(terminal,),
        deferred=(deferred,),
    )
    events: list[str] = []
    session = MagicMock()

    async def commit() -> None:
        events.append("commit")

    session.commit = AsyncMock(side_effect=commit)
    database = MagicMock()
    database.session_factory.return_value = _async_context(session)
    database.close = AsyncMock(return_value=None)
    service = MagicMock()
    service.reconcile = AsyncMock(return_value=decision)
    service.get_by_token = AsyncMock(return_value=MagicMock(run_attempts=1, dispatch_attempts=2))

    def publish(envelope: TaskEnvelope) -> None:
        events.append(f"publish:{envelope.task_kind.value}")
        if envelope is second:
            raise ConnectionError("broker offline")

    with (
        patch.object(import_tasks, "get_settings", return_value=_settings()),
        patch.object(import_tasks, "Database", return_value=database),
        patch.object(import_tasks, "ImportTaskService", return_value=service),
        patch.object(import_tasks, "_sync_terminal_business", AsyncMock()) as sync_terminal,
        patch.object(import_tasks, "_add_retry_audit", AsyncMock()) as retry_audit,
    ):
        result = asyncio.run(import_tasks._reconcile(publish))

    assert events == ["commit", "publish:preview", "publish:confirm"]
    assert result == import_tasks.ReconcileRun(
        selected=4,
        published=1,
        publish_failed=1,
        terminal=1,
        deferred=1,
    )
    sync_terminal.assert_awaited_once_with(
        session,
        terminal,
        reason="persisted_attempts_exhausted",
    )
    assert retry_audit.await_count == 3
    database.close.assert_awaited_once_with()


def test_task_envelope_rejects_kind_target_mismatch_before_claim() -> None:
    job_id = uuid4()
    token = uuid4()
    envelope = import_tasks._envelope(
        ImportTaskKind.CONFIRM,
        job_id,
        token,
        preview_revision=3,
    )
    assert envelope.task_token == token
    assert envelope.import_job_id == job_id
    assert envelope.preview_revision == 3
    assert isinstance(envelope.task_token, UUID)


def test_retry_and_terminal_audits_use_only_whitelisted_task_metadata() -> None:
    session = MagicMock()
    job = MagicMock(
        id=uuid4(),
        department_id=uuid4(),
        operator_id=uuid4(),
        status=ImportJobStatus.CONFIRM_QUEUED,
    )
    repository = MagicMock()
    repository.get_import_job = AsyncMock(return_value=job)
    audit = MagicMock()
    confirm = _envelope(ImportTaskKind.CONFIRM)
    job.confirm_task_id = str(confirm.task_token)
    job.confirmed_revision = confirm.preview_revision

    with (
        patch.object(import_tasks, "ImportRepository", return_value=repository),
        patch.object(import_tasks, "AuditRepository", return_value=audit),
    ):
        asyncio.run(
            import_tasks._add_retry_audit(
                session,
                confirm,
                reason="transient_worker_failure",
                run_attempt=1,
                dispatch_attempt=2,
            )
        )
        retry_call = audit.add.call_args.kwargs
        assert retry_call["action"] is AuditAction.IMPORT_BATCH_RETRIED
        assert retry_call["result"] is AuditResult.SUCCESS
        assert set(retry_call["after"]) == {
            "task_token",
            "task_kind",
            "run_attempt",
            "dispatch_attempt",
            "reason",
        }

        audit.reset_mock()
        asyncio.run(
            import_tasks._sync_terminal_business(
                session,
                confirm,
                reason="run_attempts_exhausted",
            )
        )
        failed_call = audit.add.call_args.kwargs
        assert failed_call["action"] is AuditAction.IMPORT_BATCH_FAILED
        assert failed_call["result"] is AuditResult.FAILED
        assert set(failed_call["after"]) == {"task_token", "task_kind", "reason"}
        assert not {
            "raw_rows",
            "contacts",
            "file_path",
            "merge_plan",
            "secret",
        }.intersection(failed_call["after"])
