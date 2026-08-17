"""Thin Celery dispatcher for durable Candidate Pool run materialization."""

import asyncio
from uuid import UUID

from celery import Celery


class TargetingTaskDispatcher:
    """Publish only a persisted run identifier to the targeting queue."""

    def __init__(self, celery_client: Celery) -> None:
        self.celery_client = celery_client

    async def materialize(self, run_id: UUID) -> None:
        token = str(UUID(str(run_id)))
        await asyncio.to_thread(
            self.celery_client.send_task,
            "targeting.materialize_candidate_pool_run",
            kwargs={"run_id": token},
            task_id=token,
            queue="targeting",
            retry=False,
        )
