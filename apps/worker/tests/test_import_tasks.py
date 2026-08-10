from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.celery_app import celery_app
from app.tasks import imports as import_tasks


def test_import_tasks_are_registered_on_the_dedicated_queue() -> None:
    parse_task = celery_app.tasks["imports.parse_import_job"]
    confirm_task = celery_app.tasks["imports.confirm_import_job"]

    assert parse_task.ignore_result is True
    assert parse_task.acks_late is True
    assert parse_task.reject_on_worker_lost is True
    assert confirm_task.ignore_result is True
    assert confirm_task.acks_late is True
    assert confirm_task.reject_on_worker_lost is True
    assert celery_app.conf.task_routes["imports.parse_import_job"] == {"queue": "import"}
    assert celery_app.conf.task_routes["imports.confirm_import_job"] == {"queue": "import"}


def test_parse_task_delegates_only_the_validated_job_id() -> None:
    job_id = uuid4()
    delegate = AsyncMock(return_value=None)

    with patch.object(import_tasks, "_parse", delegate):
        assert import_tasks.parse_import_job.run(str(job_id)) is None

    delegate.assert_awaited_once_with(job_id)


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
