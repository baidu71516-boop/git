import asyncio
from unittest.mock import MagicMock
from uuid import UUID

from app.http.targeting_tasks import TargetingTaskDispatcher


def test_materialize_dispatches_only_the_durable_run_uuid() -> None:
    celery_client = MagicMock()
    dispatcher = TargetingTaskDispatcher(celery_client)
    run_id = UUID("00000000-0000-0000-0000-000000000303")

    asyncio.run(dispatcher.materialize(run_id))

    celery_client.send_task.assert_called_once_with(
        "targeting.materialize_candidate_pool_run",
        kwargs={"run_id": str(run_id)},
        task_id=str(run_id),
        queue="targeting",
        retry=False,
    )
