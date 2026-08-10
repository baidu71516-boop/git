"""Opt-in verification of the user-supplied Huitun export without copying it."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, Operator
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.adapters import HuitunCsvAdapter
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobStatus,
    ImportSourceType,
    StoredFileType,
)
from backend_core.imports.mappings import HUITUN_FIELD_MAPPING
from backend_core.imports.models import CollectionJob, ImportJob, StoredImportFile
from backend_core.imports.parsers import ParserLimits, parse_csv
from backend_core.imports.planner import build_preview_context
from backend_core.imports.processor import ImportProcessor
from backend_core.imports.storage import LocalStorageAdapter
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

EXPECTED_SHA256 = "7efb5d3be56ccd36c5eb1a29aa179bd36277e0ab745d88bb7331b65d047776f4"


def _external_sample_path() -> Path:
    configured = os.environ.get("HUITUN_REAL_SAMPLE_PATH")
    if not configured:
        pytest.skip("HUITUN_REAL_SAMPLE_PATH is not set", allow_module_level=True)
    path = Path(configured).expanduser().resolve()
    if not path.is_file():
        pytest.fail("HUITUN_REAL_SAMPLE_PATH does not point to a file")
    repository = Path(__file__).resolve().parents[2]
    if path == repository or repository in path.parents:
        pytest.fail("The real Huitun sample must remain outside the repository")
    return path


SAMPLE_PATH = _external_sample_path()


async def _one_chunk(content: bytes) -> AsyncIterator[bytes]:
    yield content


def test_real_huitun_sample_preview_confirm_and_aggregate_facts() -> None:
    async def scenario() -> None:
        content = SAMPLE_PATH.read_bytes()
        table = parse_csv(content, "text/csv")
        assert table.headers == list(HUITUN_FIELD_MAPPING)
        assert table.encoding == "utf-8-sig"
        assert table.delimiter == ","
        assert len(table.rows) == 50

        adapter = HuitunCsvAdapter()
        adapter.mapping_for_headers(table.headers)
        adapted = [adapter.adapt(row) for row in table.rows]
        assert all(row.is_valid for row in adapted)
        assert sum(bool(row.record.contacts) for row in adapted) == 9
        assert sum(not row.record.contacts for row in adapted) == 41
        assert not any(row.warnings or row.errors for row in adapted)
        identities = [row.record.platform_identity.platform_account_id for row in adapted]
        assert len(set(identities)) == 50
        duplicate_rows, duplicate_emails = build_preview_context(
            [(row.row_number, row.record) for row in adapted]
        )
        assert duplicate_rows == {}
        assert duplicate_emails == set()

        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            with TemporaryDirectory() as directory:
                storage = LocalStorageAdapter(Path(directory))
                stored = await storage.store(
                    _one_chunk(content), suffix=".csv", max_bytes=25 * 1024 * 1024
                )
                assert stored.sha256 == EXPECTED_SHA256
                async with factory() as session:
                    department = Department(
                        name="真实样本隔离验收部门",
                        password_hash="not-used-by-worker",
                        status=DepartmentStatus.ACTIVE,
                        session_days=30,
                    )
                    session.add(department)
                    await session.flush()
                    operator = Operator(
                        department_id=department.id,
                        name="隔离验收操作人",
                        role=Role.OPERATOR,
                        status=OperatorStatus.ACTIVE,
                    )
                    session.add(operator)
                    await session.flush()
                    collection = CollectionJob(
                        name="真实样本隔离验收",
                        industry="测试",
                        purpose="Phase 1B 外部文件验收",
                        target_action="Preview 后 Confirm",
                        target_count=50,
                        department_id=department.id,
                        owner_operator_id=operator.id,
                        source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
                        status=CollectionJobStatus.ACTIVE,
                    )
                    session.add(collection)
                    stored_file = StoredImportFile(
                        sha256=stored.sha256,
                        storage_key=stored.storage_key,
                        size=stored.size,
                        detected_type=StoredFileType.CSV,
                        detected_mime="text/csv",
                        expires_at=datetime.now(UTC) + timedelta(days=30),
                    )
                    session.add(stored_file)
                    await session.flush()
                    job = ImportJob(
                        collection_job_id=collection.id,
                        department_id=department.id,
                        operator_id=operator.id,
                        stored_file_id=stored_file.id,
                        original_filename=SAMPLE_PATH.name,
                        mime_type="text/csv",
                        file_size=stored.size,
                        sha256=stored.sha256,
                        source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
                        status=ImportJobStatus.UPLOADED,
                        preview_revision=0,
                        parse_task_id="real-sample-preview",
                    )
                    session.add(job)
                    await session.commit()

                    processor = ImportProcessor(session, storage, parser_limits=ParserLimits())
                    preview = await processor.parse_and_preview(job.id)
                    assert preview == {
                        "import_job_id": str(job.id),
                        "status": ImportJobStatus.PREVIEW_READY.value,
                        "preview_revision": 1,
                    }
                    refreshed = await session.get(ImportJob, job.id)
                    assert refreshed is not None
                    assert refreshed.preview_summary == {
                        "file_type": "csv",
                        "field_count": 37,
                        "total_rows": 50,
                        "valid_rows": 50,
                        "warning_rows": 0,
                        "error_rows": 0,
                        "created_rows": 50,
                        "updated_rows": 0,
                        "no_change_rows": 0,
                        "skipped_rows": 0,
                        "manual_review_rows": 0,
                        "existing_rows": 0,
                        "valid_email_rows": 9,
                        "invalid_email_rows": 0,
                        "missing_email_rows": 41,
                        "possible_duplicate_contact_rows": 0,
                    }
                    assert await session.scalar(select(func.count()).select_from(Influencer)) == 0

                    refreshed.status = ImportJobStatus.CONFIRM_QUEUED
                    refreshed.confirmed_revision = 1
                    refreshed.confirm_task_id = "real-sample-confirm"
                    await session.commit()
                    result = await processor.confirm(job.id, 1)
                    assert result["created_rows"] == 50
                    assert result["error_rows"] == 0
                    assert await processor.confirm(job.id, 1) == result
                    assert await session.scalar(select(func.count()).select_from(Influencer)) == 50
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(InfluencerPlatformAccount)
                        )
                        == 50
                    )
                    assert (
                        await session.scalar(select(func.count()).select_from(InfluencerContact))
                        == 9
                    )
                    assert (
                        await session.scalar(
                            select(func.count()).select_from(InfluencerMetricSnapshot)
                        )
                        == 50
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
