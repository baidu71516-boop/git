"""Thin Celery dispatcher for import task entrypoints."""

import asyncio
from uuid import UUID

from celery import Celery


class ImportTaskDispatcher:
    def __init__(self, celery_client: Celery) -> None:
        self.celery_client = celery_client

    async def parse(self, import_job_id: UUID, task_id: str) -> None:
        await asyncio.to_thread(
            self.celery_client.send_task,
            "imports.parse_import_job",
            args=[str(import_job_id)],
            task_id=task_id,
            queue="import",
        )

    async def parse_file(
        self,
        import_job_id: UUID,
        import_job_file_id: UUID,
        task_id: str,
    ) -> None:
        """Dispatch persisted file state without putting source data in the broker."""

        await asyncio.to_thread(
            self.celery_client.send_task,
            "imports.parse_import_job_file",
            args=[str(import_job_id), str(import_job_file_id), task_id],
            task_id=task_id,
            queue="import",
        )

    async def confirm(self, import_job_id: UUID, preview_revision: int, task_id: str) -> None:
        await asyncio.to_thread(
            self.celery_client.send_task,
            "imports.confirm_import_job",
            args=[str(import_job_id), preview_revision],
            task_id=task_id,
            queue="import",
        )
