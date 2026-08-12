import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.celery_app import celery_app
from app.tasks import imports as import_tasks


def test_import_tasks_are_registered_on_the_dedicated_queue() -> None:
    parse_task = celery_app.tasks["imports.parse_import_job"]
    parse_file_task = celery_app.tasks["imports.parse_import_job_file"]
    preview_task = celery_app.tasks["imports.preview_import_job"]
    confirm_task = celery_app.tasks["imports.confirm_import_job"]

    assert parse_task.ignore_result is True
    assert parse_task.acks_late is True
    assert parse_task.reject_on_worker_lost is True
    assert parse_file_task.ignore_result is True
    assert parse_file_task.acks_late is True
    assert parse_file_task.reject_on_worker_lost is True
    assert preview_task.ignore_result is True
    assert preview_task.acks_late is True
    assert preview_task.reject_on_worker_lost is True
    assert preview_task.max_retries == import_tasks.HEAVY_IMPORT_MAX_RETRIES
    assert confirm_task.ignore_result is True
    assert confirm_task.acks_late is True
    assert confirm_task.reject_on_worker_lost is True
    assert celery_app.conf.task_routes["imports.parse_import_job"] == {"queue": "import"}
    assert celery_app.conf.task_routes["imports.parse_import_job_file"] == {"queue": "import"}
    assert celery_app.conf.task_routes["imports.preview_import_job"] == {"queue": "import"}
    assert celery_app.conf.task_routes["imports.confirm_import_job"] == {"queue": "import"}
    assert celery_app.conf.worker_concurrency == 2
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_parse_task_delegates_only_the_validated_job_id() -> None:
    job_id = uuid4()
    delegate = AsyncMock(return_value=None)

    with patch.object(import_tasks, "_parse", delegate):
        assert import_tasks.parse_import_job.run(str(job_id)) is None

    delegate.assert_awaited_once_with(job_id)


def test_parse_file_task_delegates_only_validated_identifiers_and_token() -> None:
    job_id = uuid4()
    file_id = uuid4()
    task_id = uuid4().hex
    delegate = AsyncMock(return_value=None)

    with patch.object(import_tasks, "_parse_file", delegate):
        assert import_tasks.parse_import_job_file.run(str(job_id), str(file_id), task_id) is None

    delegate.assert_awaited_once_with(job_id, file_id, task_id)


def test_preview_task_delegates_only_validated_job_id_and_token() -> None:
    job_id = uuid4()
    task_id = uuid4().hex
    delegate = AsyncMock(return_value=True)

    with patch.object(import_tasks, "_preview", delegate):
        assert import_tasks.preview_import_job.run(str(job_id), task_id) is None

    delegate.assert_awaited_once_with(job_id, task_id)


def test_preview_task_retries_when_the_heavy_import_gate_is_busy() -> None:
    job_id = uuid4()
    task_id = uuid4().hex
    delegate = AsyncMock(return_value=False)

    with (
        patch.object(import_tasks, "_preview", delegate),
        patch.object(
            import_tasks.preview_import_job,
            "retry",
            side_effect=RuntimeError("synthetic retry"),
        ) as retry,
        pytest.raises(RuntimeError, match="synthetic retry"),
    ):
        import_tasks.preview_import_job.run(str(job_id), task_id)

    delegate.assert_awaited_once_with(job_id, task_id)
    retry.assert_called_once_with(countdown=import_tasks.HEAVY_IMPORT_RETRY_DELAY_SECONDS)


def test_preview_task_marks_failed_without_requeue_after_bounded_retries() -> None:
    job_id = uuid4()
    task_id = uuid4().hex
    delegate = AsyncMock(return_value=False)

    import_tasks.preview_import_job.push_request(retries=import_tasks.HEAVY_IMPORT_MAX_RETRIES)
    try:
        with (
            patch.object(import_tasks, "_preview", delegate),
            patch.object(import_tasks.preview_import_job, "retry") as retry,
        ):
            assert import_tasks.preview_import_job.run(str(job_id), task_id) is None
    finally:
        import_tasks.preview_import_job.pop_request()

    delegate.assert_awaited_once_with(
        job_id,
        task_id,
        mark_retry_exhausted=True,
    )
    retry.assert_not_called()


def test_preview_core_holds_and_releases_session_lock_on_one_connection() -> None:
    job_id = uuid4()
    task_id = uuid4().hex
    events: list[str] = []

    async def acquire(*args: object, **kwargs: object) -> bool:
        _ = (args, kwargs)
        events.append("acquire")
        return True

    async def unlock(*args: object, **kwargs: object) -> None:
        _ = (args, kwargs)
        events.append("unlock")

    async def close() -> None:
        events.append("close")

    async def build(*args: object, **kwargs: object) -> dict[str, object]:
        _ = (args, kwargs)
        events.append("build")
        return {}

    connection = MagicMock()
    connection.scalar = AsyncMock(side_effect=acquire)
    connection.execute = AsyncMock(side_effect=unlock)
    connection.commit = AsyncMock(return_value=None)
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(return_value=connection)
    connection_context.__aexit__ = AsyncMock(return_value=None)
    database = MagicMock()
    database.engine.connect.return_value = connection_context
    database.close = AsyncMock(side_effect=close)

    session = MagicMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=None)
    session_factory = MagicMock(return_value=session_context)

    processor = MagicMock()
    processor.build = AsyncMock(side_effect=build)
    processor_factory = MagicMock(return_value=processor)
    preview_module = ModuleType("backend_core.imports.preview_processor")
    preview_module.UnifiedPreviewProcessor = processor_factory  # type: ignore[attr-defined]
    storage = MagicMock()

    with (
        patch.dict(sys.modules, {preview_module.__name__: preview_module}),
        patch.object(import_tasks, "Database", return_value=database),
        patch.object(import_tasks, "AsyncSession", session_factory),
        patch.object(import_tasks, "LocalStorageAdapter", return_value=storage),
    ):
        assert asyncio.run(import_tasks._preview(job_id, task_id)) is True

    session_factory.assert_called_once_with(bind=connection, expire_on_commit=False)
    processor_factory.assert_called_once()
    processor_args = processor_factory.call_args.args
    assert processor_args[0] is session
    assert processor_args[1] is storage
    processor.build.assert_awaited_once_with(job_id, task_id)
    assert events == ["acquire", "build", "unlock", "close"]
    acquire_params = connection.scalar.await_args.args
    unlock_params = connection.execute.await_args.args
    assert "pg_try_advisory_lock" in str(acquire_params[0])
    assert "pg_advisory_unlock" in str(unlock_params[0])
    assert (
        acquire_params[1]
        == unlock_params[1]
        == {"lock_key": import_tasks.HEAVY_IMPORT_PREVIEW_LOCK_KEY}
    )


def test_preview_core_marks_current_token_failed_when_gate_retries_are_exhausted() -> None:
    job_id = uuid4()
    task_id = uuid4().hex
    connection = MagicMock()
    connection.scalar = AsyncMock(return_value=False)
    connection.commit = AsyncMock(return_value=None)
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(return_value=connection)
    connection_context.__aexit__ = AsyncMock(return_value=None)
    database = MagicMock()
    database.engine.connect.return_value = connection_context
    database.close = AsyncMock(return_value=None)

    session = MagicMock()
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=None)
    session_factory = MagicMock(return_value=session_context)
    processor = MagicMock()
    processor.mark_failed_after_retry_exhausted = AsyncMock(return_value=None)
    processor_factory = MagicMock(return_value=processor)
    preview_module = ModuleType("backend_core.imports.preview_processor")
    preview_module.UnifiedPreviewProcessor = processor_factory  # type: ignore[attr-defined]

    with (
        patch.dict(sys.modules, {preview_module.__name__: preview_module}),
        patch.object(import_tasks, "Database", return_value=database),
        patch.object(import_tasks, "AsyncSession", session_factory),
        patch.object(import_tasks, "LocalStorageAdapter", return_value=MagicMock()),
    ):
        assert (
            asyncio.run(
                import_tasks._preview(
                    job_id,
                    task_id,
                    mark_retry_exhausted=True,
                )
            )
            is False
        )

    processor.mark_failed_after_retry_exhausted.assert_awaited_once_with(job_id, task_id)
    connection.execute.assert_not_called()
    database.close.assert_awaited_once_with()


def test_confirm_task_delegates_job_id_and_revision() -> None:
    job_id = uuid4()
    delegate = AsyncMock(return_value=None)

    with patch.object(import_tasks, "_confirm", delegate):
        assert import_tasks.confirm_import_job.run(str(job_id), 7) is None

    delegate.assert_awaited_once_with(job_id, 7)


def test_invalid_task_identifier_is_rejected_before_core_execution() -> None:
    delegate = AsyncMock(return_value=None)

    with patch.object(import_tasks, "_parse", delegate), pytest.raises(ValueError):
        import_tasks.parse_import_job.run("not-a-uuid")

    delegate.assert_not_awaited()

    preview_delegate = AsyncMock(return_value=True)
    with patch.object(import_tasks, "_preview", preview_delegate), pytest.raises(ValueError):
        import_tasks.preview_import_job.run("not-a-uuid", uuid4().hex)
    preview_delegate.assert_not_awaited()


@pytest.mark.parametrize(
    ("job_id", "file_id"),
    [
        ("not-a-uuid", str(uuid4())),
        (str(uuid4()), "not-a-uuid"),
    ],
)
def test_parse_file_rejects_invalid_resource_identifiers(
    job_id: str,
    file_id: str,
) -> None:
    delegate = AsyncMock(return_value=None)

    with patch.object(import_tasks, "_parse_file", delegate), pytest.raises(ValueError):
        import_tasks.parse_import_job_file.run(job_id, file_id, uuid4().hex)

    delegate.assert_not_awaited()
