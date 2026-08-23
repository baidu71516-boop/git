"""Thin Celery dispatcher for durable Content Activity refresh requests."""

import asyncio
from uuid import UUID

from celery import Celery


class ContentActivityTaskDispatcher:
    """Publish only a persisted refresh-request UUID to the analytics queue."""

    def __init__(self, celery_client: Celery) -> None:
        self.celery_client = celery_client

    async def refresh(self, request_token: UUID) -> None:
        token = str(UUID(str(request_token)))
        await asyncio.to_thread(
            self.celery_client.send_task,
            "content_activity.refresh_request",
            args=[token],
            task_id=token,
            queue="analytics",
            retry=False,
        )
