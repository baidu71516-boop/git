"""Transaction coupling between import business writes and durable task completion."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from backend_core.config.settings import Settings
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.enums import (
    ImportJobStatus,
    ImportSourceType,
    ImportTaskKind,
    ImportTaskState,
)
from backend_core.imports.models import ImportJob, ImportTaskRequest
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.processor import ImportProcessor
from backend_core.imports.storage import LocalStorageAdapter
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_task_completion_rolls_back_and_commits_with_business_transaction() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        with TemporaryDirectory(prefix="task-atomicity-") as directory:
            async with factory() as session:
                job = ImportJob(
                    collection_job_id=uuid4(),
                    department_id=uuid4(),
                    operator_id=uuid4(),
                    source_type=ImportSourceType.GENERIC_CSV,
                    status=ImportJobStatus.DRAFT,
                    preview_revision=0,
                )
                session.add(job)
                await session.flush()
                token = uuid4()
                task = ImportTaskRequest(
                    task_token=token,
                    task_kind=ImportTaskKind.PREVIEW,
                    import_job_id=job.id,
                    state=ImportTaskState.RUNNING,
                    dispatch_attempts=1,
                    run_attempts=1,
                    requested_at=datetime.now(UTC),
                    started_at=datetime.now(UTC),
                    lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
                session.add(task)
                await session.commit()

                processor = ImportProcessor(
                    session,
                    LocalStorageAdapter(Path(directory)),
                    parser_limits=ParserLimits(),
                    task_settings=Settings(_env_file=None),
                )
                job.error_code = "would-have-been-visible"
                await processor._complete_task((token, 1))
                await session.rollback()
                await session.refresh(job)
                await session.refresh(task)
                assert job.error_code is None
                assert task.state is ImportTaskState.RUNNING
                assert task.completed_at is None

                job.error_code = "atomically-visible"
                await processor._complete_task((token, 1))
                await session.commit()
                await session.refresh(job)
                await session.refresh(task)
                assert job.error_code == "atomically-visible"
                assert task.state is ImportTaskState.COMPLETED
                assert task.completed_at is not None
        await engine.dispose()

    asyncio.run(scenario())


def test_task_context_requires_token_and_positive_generation_together() -> None:
    token = uuid4()
    assert ImportProcessor._task_context(None, None) is None
    assert ImportProcessor._task_context(token, 2) == (token, 2)
    for values in ((token, None), (None, 1), (token, 0)):
        try:
            ImportProcessor._task_context(*values)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid partial task context was accepted")
