import asyncio
from unittest.mock import MagicMock
from uuid import uuid4

from app.http.import_tasks import ImportTaskDispatcher


def test_parse_file_dispatches_only_ids_and_persisted_task_token() -> None:
    celery_client = MagicMock()
    dispatcher = ImportTaskDispatcher(celery_client)
    job_id = uuid4()
    file_id = uuid4()
    task_id = uuid4().hex

    asyncio.run(dispatcher.parse_file(job_id, file_id, task_id))

    celery_client.send_task.assert_called_once_with(
        "imports.parse_import_job_file",
        args=[str(job_id), str(file_id), task_id],
        task_id=task_id,
        queue="import",
    )


def test_preview_dispatches_only_job_id_and_persisted_task_token() -> None:
    celery_client = MagicMock()
    dispatcher = ImportTaskDispatcher(celery_client)
    job_id = uuid4()
    task_id = uuid4().hex

    asyncio.run(dispatcher.preview(job_id, task_id))

    celery_client.send_task.assert_called_once_with(
        "imports.preview_import_job",
        args=[str(job_id), task_id],
        task_id=task_id,
        queue="import",
    )


def test_legacy_dispatch_payloads_remain_unchanged() -> None:
    celery_client = MagicMock()
    dispatcher = ImportTaskDispatcher(celery_client)
    job_id = uuid4()
    task_id = uuid4().hex

    asyncio.run(dispatcher.parse(job_id, task_id))
    asyncio.run(dispatcher.confirm(job_id, 3, task_id))

    assert celery_client.send_task.call_args_list[0].kwargs == {
        "args": [str(job_id)],
        "task_id": task_id,
        "queue": "import",
    }
    assert celery_client.send_task.call_args_list[1].kwargs == {
        "args": [str(job_id), 3],
        "task_id": task_id,
        "queue": "import",
    }
