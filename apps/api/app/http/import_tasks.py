"""Thin Celery dispatcher for import task entrypoints."""

import asyncio
from uuid import UUID

from celery import Celery


def _canonical_task_token(task_id: str | UUID) -> str:
    return str(UUID(str(task_id)))


class ImportTaskDispatcher:
    def __init__(self, celery_client: Celery) -> None:
        self.celery_client = celery_client

    async def parse(self, import_job_id: UUID, task_id: str) -> None:
        token = _canonical_task_token(task_id)
        await asyncio.to_thread(
            self.celery_client.send_task,
            "imports.parse_import_job",
            args=[str(import_job_id), token],
            task_id=token,
            queue="import",
            retry=False,
        )

    async def parse_file(
        self,
        import_job_id: UUID,
        import_job_file_id: UUID,
        task_id: str,
    ) -> None:
        """Dispatch persisted file state without putting source data in the broker."""

        token = _canonical_task_token(task_id)
        await asyncio.to_thread(
            self.celery_client.send_task,
            "imports.parse_import_job_file",
            args=[str(import_job_id), str(import_job_file_id), token],
            task_id=token,
            queue="import",
            retry=False,
        )

    async def preview(self, import_job_id: UUID, task_id: str) -> None:
        """Dispatch a persisted unified Preview using only its identifiers."""

        token = _canonical_task_token(task_id)
        await asyncio.to_thread(
            self.celery_client.send_task,
            "imports.preview_import_job",
            args=[str(import_job_id), token],
            task_id=token,
            queue="import",
            retry=False,
        )

    async def confirm(self, import_job_id: UUID, preview_revision: int, task_id: str) -> None:
        token = _canonical_task_token(task_id)
        await asyncio.to_thread(
            self.celery_client.send_task,
            "imports.confirm_import_job",
            args=[str(import_job_id), preview_revision, token],
            task_id=token,
            queue="import",
            retry=False,
        )
