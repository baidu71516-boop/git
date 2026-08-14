"""Real PostgreSQL gate for the worker heartbeat execution boundary."""

from __future__ import annotations

import asyncio
import importlib
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.tasks import imports as import_tasks
from backend_core.config import Settings
from backend_core.db import Database
from backend_core.imports.enums import ImportTaskKind, ImportTaskState
from backend_core.imports.models import ImportTaskRequest
from backend_core.imports.task_service import ClaimStatus, ImportTaskService, TaskEnvelope
from sqlalchemy import select, text

INTEGRATION_TESTS = Path(__file__).resolve().parents[3] / "tests" / "integration"
sys.path.insert(0, str(INTEGRATION_TESTS))
preview_gate = importlib.import_module("test_unified_preview_postgres")


def test_with_heartbeat_renews_during_blocking_operation_on_independent_runtime() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            seeded = await preview_gate._seed_batch(harness, [[]])
            async with harness.factory() as session:
                schema_name = str(await session.scalar(text("SELECT current_schema()")))
            scoped_url = preview_gate.TEST_DATABASE_URL.update_query_dict(
                {"options": f"-csearch_path={schema_name}"}
            )
            database_url = scoped_url.render_as_string(hide_password=False)
            settings = Settings(
                _env_file=None,
                app_env="test",
                database_url=database_url,
                import_task_lease_seconds=2,
                import_task_heartbeat_seconds=1,
            )
            database = Database(database_url)
            try:
                async with database.session_factory() as session:
                    service = ImportTaskService(session, settings)
                    task = await service.create_request(
                        task_kind=ImportTaskKind.PREVIEW,
                        import_job_id=seeded.job_id,
                        task_token=uuid4(),
                    )
                    dispatch = await service.prepare_dispatch(task.task_token)
                    assert dispatch.envelope is not None
                    envelope = dispatch.envelope
                    await session.commit()
                async with database.session_factory() as session:
                    claim = await ImportTaskService(session, settings).claim(envelope)
                    assert claim.status is ClaimStatus.CLAIMED
                    assert claim.generation == 1
                    await session.commit()
                async with database.session_factory() as session:
                    claimed = await session.scalar(
                        select(ImportTaskRequest).where(
                            ImportTaskRequest.task_token == envelope.task_token
                        )
                    )
                    assert claimed is not None and claimed.lease_expires_at is not None
                    original_lease = claimed.lease_expires_at

                async def blocking_operation() -> object:
                    # This models a parser/library call that blocks the worker's
                    # main asyncio loop. A same-loop heartbeat cannot run here.
                    time.sleep(3.25)  # noqa: ASYNC251 - intentional starvation gate
                    return None

                await import_tasks._with_heartbeat(
                    database,
                    settings,
                    TaskEnvelope.from_payload(
                        task_token=envelope.task_token,
                        task_kind=envelope.task_kind,
                        import_job_id=envelope.import_job_id,
                    ),
                    1,
                    blocking_operation,
                )

                async with database.session_factory() as session:
                    task = await session.scalar(
                        select(ImportTaskRequest).where(
                            ImportTaskRequest.task_token == envelope.task_token
                        )
                    )
                    database_now = await session.scalar(text("SELECT clock_timestamp()"))
                    assert task is not None and task.lease_expires_at is not None
                    assert isinstance(database_now, datetime)
                    assert task.lease_expires_at > original_lease
                    assert task.lease_expires_at > database_now.astimezone(UTC)
                    completed = await ImportTaskService(session, settings).complete(
                        envelope.task_token,
                        1,
                    )
                    assert completed is True
                    await session.commit()
                async with database.session_factory() as session:
                    completed_task = await session.scalar(
                        select(ImportTaskRequest).where(
                            ImportTaskRequest.task_token == envelope.task_token
                        )
                    )
                    assert completed_task is not None
                    assert completed_task.state is ImportTaskState.COMPLETED
                    assert completed_task.completed_at is not None
            finally:
                await database.close()

    asyncio.run(scenario())
