"""Thin Celery entrypoints for persisted legacy and bulk import jobs."""

import asyncio
from uuid import UUID

from backend_core.config import Settings, get_settings
from backend_core.db import Database
from backend_core.imports.batch_processor import BatchImportProcessor
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.processor import ImportProcessor
from backend_core.imports.storage import LocalStorageAdapter

from app.celery_app import celery_app


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


async def _parse(import_job_id: UUID) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session:
            processor = ImportProcessor(
                session,
                LocalStorageAdapter(settings.import_data_dir),
                parser_limits=_parser_limits(settings),
            )
            await processor.parse_and_preview(import_job_id)
    finally:
        await database.close()


async def _parse_file(
    import_job_id: UUID,
    import_job_file_id: UUID,
    task_id: str,
) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session:
            processor = BatchImportProcessor(
                session,
                LocalStorageAdapter(settings.import_data_dir),
                parser_limits=_parser_limits(settings),
                max_batch_rows=settings.import_max_batch_rows,
            )
            await processor.parse_file(import_job_id, import_job_file_id, task_id)
    finally:
        await database.close()


async def _confirm(import_job_id: UUID, preview_revision: int) -> None:
    settings = get_settings()
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session:
            processor = ImportProcessor(
                session,
                LocalStorageAdapter(settings.import_data_dir),
                parser_limits=_parser_limits(settings),
            )
            await processor.confirm(import_job_id, preview_revision)
    finally:
        await database.close()


@celery_app.task(
    name="imports.parse_import_job",
    bind=False,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)  # type: ignore[untyped-decorator]
def parse_import_job(import_job_id: str) -> None:
    """Parse and persist a Preview; the task payload contains only the Job ID."""

    asyncio.run(_parse(UUID(import_job_id)))


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
    task_id: str,
) -> None:
    """Parse one occurrence from persisted state using an ID-only broker payload."""

    asyncio.run(_parse_file(UUID(import_job_id), UUID(import_job_file_id), task_id))


@celery_app.task(
    name="imports.confirm_import_job",
    bind=False,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)  # type: ignore[untyped-decorator]
def confirm_import_job(import_job_id: str, preview_revision: int) -> None:
    """Validate and commit one persisted Preview revision atomically."""

    asyncio.run(_confirm(UUID(import_job_id), preview_revision))
