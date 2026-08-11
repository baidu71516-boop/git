import asyncio
import csv
import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID

import pytest
from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, Operator
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.enums import (
    CollectionJobStatus,
    ImportJobFailedStage,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportRowAction,
    ImportSourceType,
    SourceAcquiredAtOrigin,
    StoredFileType,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.mappings import HUITUN_FIELD_MAPPING
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportRow,
    StoredImportFile,
)
from backend_core.imports.parsers import ParserLimits
from backend_core.imports.processor import ImportProcessor
from backend_core.imports.storage import LocalStorageAdapter
from backend_core.influencers.enums import DataSource, Platform
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    PlatformAccountSourceIdentity,
)
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@asynccontextmanager
async def processor_session() -> AsyncIterator[tuple[AsyncSession, LocalStorageAdapter]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with TemporaryDirectory() as directory:
        async with factory() as session:
            yield session, LocalStorageAdapter(Path(directory))
    await engine.dispose()


async def one_chunk(content: bytes) -> AsyncIterator[bytes]:
    yield content


def huitun_csv(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(HUITUN_FIELD_MAPPING))
    writer.writeheader()
    for overrides in rows:
        row = {header: "--" for header in HUITUN_FIELD_MAPPING}
        row.update(overrides)
        writer.writerow(row)
    return stream.getvalue().encode("utf-8-sig")


def huitun_xlsx(rows: list[dict[str, str]]) -> bytes:
    stream = io.BytesIO()
    workbook = Workbook()
    try:
        worksheet = workbook.active
        worksheet.append(list(HUITUN_FIELD_MAPPING))
        for overrides in rows:
            row = {header: "--" for header in HUITUN_FIELD_MAPPING}
            row.update(overrides)
            worksheet.append([row[header] for header in HUITUN_FIELD_MAPPING])
        workbook.save(stream)
    finally:
        workbook.close()
    return stream.getvalue()


async def seed_import_job(
    session: AsyncSession,
    storage: LocalStorageAdapter,
    content: bytes,
    *,
    filename: str = "sanitized-fixture.csv",
    detected_type: StoredFileType = StoredFileType.CSV,
    mime_type: str = "text/csv",
    source_type: ImportSourceType = ImportSourceType.MANUAL_HUITUN_EXPORT,
    field_mapping: dict[str, str] | None = None,
) -> ImportJob:
    stored = await storage.store(
        one_chunk(content),
        suffix=Path(filename).suffix,
        max_bytes=25 * 1024 * 1024,
    )
    department = Department(
        name=f"Processor fixture {stored.sha256[:8]}",
        password_hash="not-used-by-worker",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name="脱敏操作人",
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    session.add(operator)
    await session.flush()
    collection = CollectionJob(
        name="脱敏采集任务",
        industry="测试行业",
        purpose="验证两阶段导入",
        target_action="预览后确认",
        target_count=10,
        department_id=department.id,
        owner_operator_id=operator.id,
        source_type=source_type,
        status=CollectionJobStatus.ACTIVE,
    )
    session.add(collection)
    stored_file = StoredImportFile(
        sha256=stored.sha256,
        storage_key=stored.storage_key,
        size=stored.size,
        detected_type=detected_type,
        detected_mime=mime_type,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    session.add(stored_file)
    await session.flush()
    job = ImportJob(
        collection_job_id=collection.id,
        department_id=department.id,
        operator_id=operator.id,
        stored_file_id=stored_file.id,
        original_filename=filename,
        mime_type=mime_type,
        file_size=stored.size,
        sha256=stored.sha256,
        source_type=source_type,
        status=ImportJobStatus.UPLOADED,
        field_mapping=field_mapping,
        preview_revision=0,
        parse_task_id="parse-fixture",
    )
    session.add(job)
    await session.flush()
    session.add(
        ImportJobFile(
            import_job_id=job.id,
            stored_file_id=stored_file.id,
            position=1,
            client_file_id=f"legacy:{job.id}",
            original_filename=filename,
            declared_mime=mime_type,
            status=ImportJobFileStatus.UPLOADED,
            source_acquired_at=None,
            source_acquired_at_origin=SourceAcquiredAtOrigin.LEGACY_UNKNOWN,
            field_mapping=field_mapping,
            parse_task_id="parse-fixture",
        )
    )
    await session.commit()
    return job


def valid_row(profile_id: str, *, email: str = "--", followers: str = "1000") -> dict[str, str]:
    return {
        "达人名称": f"脱敏达人-{profile_id}",
        "达人官方地址": f"https://www.xiaohongshu.com/user/profile/{profile_id}",
        "小红书号": f"handle-{profile_id}",
        "更新时间": "2026-08-10 12:00:00",
        "联系邮箱": email,
        "粉丝数": followers,
        "灰豚指数": "88.5",
    }


def limits() -> ParserLimits:
    return ParserLimits(max_rows=1000, max_columns=100, max_cells=100_000)


async def queue_confirm(session: AsyncSession, job_id: UUID, revision: int) -> None:
    job = await session.get(ImportJob, job_id)
    assert job is not None
    job.status = ImportJobStatus.CONFIRM_QUEUED
    job.confirmed_revision = revision
    job.confirm_task_id = "confirm-fixture"
    await session.commit()


def test_preview_then_confirm_is_atomic_audited_and_idempotent() -> None:
    async def scenario() -> None:
        async with processor_session() as (session, storage):
            content = huitun_csv(
                [
                    valid_row("fixture-a", email="shared@mcn.example", followers="1234"),
                    valid_row("fixture-b", email="shared@mcn.example", followers="5678"),
                    {
                        "达人名称": "损坏身份行",
                        "达人官方地址": "https://example.com/not-xhs",
                    },
                ]
            )
            job = await seed_import_job(session, storage, content)
            job_id = job.id
            processor = ImportProcessor(session, storage, parser_limits=limits())

            preview = await processor.parse_and_preview(job_id)
            assert preview["status"] == ImportJobStatus.PREVIEW_READY.value
            assert preview["preview_revision"] == 1
            refreshed = await session.get(ImportJob, job_id)
            assert refreshed is not None
            assert refreshed.total_rows == 3
            assert refreshed.created_rows == 2
            assert refreshed.error_rows == 1
            assert await session.scalar(select(func.count()).select_from(Influencer)) == 0
            import_rows = list(
                await session.scalars(
                    select(ImportRow)
                    .where(ImportRow.import_job_id == job_id)
                    .order_by(ImportRow.row_number)
                )
            )
            occurrence = await session.scalar(
                select(ImportJobFile).where(ImportJobFile.import_job_id == job_id)
            )
            assert occurrence is not None
            assert occurrence.position == 1
            assert occurrence.client_file_id == f"legacy:{job_id}"
            assert occurrence.source_acquired_at is None
            assert occurrence.source_acquired_at_origin is SourceAcquiredAtOrigin.LEGACY_UNKNOWN
            assert occurrence.status is ImportJobFileStatus.READY
            assert {row.import_job_file_id for row in import_rows} == {occurrence.id}
            assert [row.action for row in import_rows] == [
                ImportRowAction.CREATE,
                ImportRowAction.CREATE,
                ImportRowAction.ERROR,
            ]
            assert import_rows[2].raw_data["达人名称"] == "损坏身份行"

            duplicate_parse = await processor.parse_and_preview(job_id)
            assert duplicate_parse["status"] == ImportJobStatus.PREVIEW_READY.value
            assert duplicate_parse["preview_revision"] == 1

            await queue_confirm(session, job_id, 1)
            result = await processor.confirm(job_id, 1)
            assert result["created_rows"] == 2
            assert result["error_rows"] == 1
            assert await session.scalar(select(func.count()).select_from(Influencer)) == 2
            assert (
                await session.scalar(select(func.count()).select_from(InfluencerPlatformAccount))
                == 2
            )
            contacts = list(await session.scalars(select(InfluencerContact)))
            assert len(contacts) == 2
            assert {contact.normalized_value for contact in contacts} == {"shared@mcn.example"}
            assert all(contact.possible_duplicate_contact for contact in contacts)
            assert (
                await session.scalar(select(func.count()).select_from(InfluencerMetricSnapshot))
                == 2
            )

            repeated = await processor.confirm(job_id, 1)
            assert repeated == result
            assert await session.scalar(select(func.count()).select_from(Influencer)) == 2
            assert (
                await session.scalar(select(func.count()).select_from(InfluencerMetricSnapshot))
                == 2
            )
            audit_actions = set(await session.scalars(select(AuditLog.action)))
            assert {
                AuditAction.IMPORT_PREVIEW_CREATED,
                AuditAction.IMPORT_CONFIRMED,
                AuditAction.IMPORT_COMPLETED,
            } <= audit_actions

    asyncio.run(scenario())


def test_xlsx_preview_and_confirm_keep_the_single_file_compatibility_bridge() -> None:
    async def scenario() -> None:
        async with processor_session() as (session, storage):
            content = huitun_xlsx([valid_row("xlsx-compat", followers="4321")])
            job = await seed_import_job(
                session,
                storage,
                content,
                filename="sanitized-fixture.xlsx",
                detected_type=StoredFileType.XLSX,
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            processor = ImportProcessor(session, storage, parser_limits=limits())

            preview = await processor.parse_and_preview(job.id)
            assert preview["status"] == ImportJobStatus.PREVIEW_READY.value
            assert preview["preview_revision"] == 1
            occurrence = await session.scalar(
                select(ImportJobFile).where(ImportJobFile.import_job_id == job.id)
            )
            assert occurrence is not None
            assert occurrence.status is ImportJobFileStatus.READY
            rows = list(
                await session.scalars(select(ImportRow).where(ImportRow.import_job_id == job.id))
            )
            assert len(rows) == 1
            assert rows[0].import_job_file_id == occurrence.id

            await queue_confirm(session, job.id, 1)
            result = await processor.confirm(job.id, 1)
            assert result["created_rows"] == 1
            assert await session.scalar(select(func.count()).select_from(Influencer)) == 1

    asyncio.run(scenario())


def test_confirm_detects_identity_change_and_writes_nothing_from_stale_plan() -> None:
    async def scenario() -> None:
        async with processor_session() as (session, storage):
            content = huitun_csv([valid_row("stale-identity")])
            job = await seed_import_job(session, storage, content)
            processor = ImportProcessor(session, storage, parser_limits=limits())
            await processor.parse_and_preview(job.id)

            existing = Influencer(display_name="并发创建的主体")
            session.add(existing)
            await session.flush()
            session.add(
                InfluencerPlatformAccount(
                    influencer_id=existing.id,
                    platform=Platform.XIAOHONGSHU,
                    platform_account_id="stale-identity",
                    account_name="并发账号",
                    source=DataSource.HUITUN,
                    is_active=True,
                )
            )
            await session.commit()
            await queue_confirm(session, job.id, 1)

            result = await processor.confirm(job.id, 1)

            assert result["status"] == ImportJobStatus.PREVIEW_STALE.value
            refreshed = await session.get(ImportJob, job.id)
            assert refreshed is not None and refreshed.status == ImportJobStatus.PREVIEW_STALE
            assert await session.scalar(select(func.count()).select_from(Influencer)) == 1
            assert (
                await session.scalar(select(func.count()).select_from(InfluencerMetricSnapshot))
                == 0
            )

    asyncio.run(scenario())


def test_confirm_rolls_back_all_business_writes_when_one_row_fails() -> None:
    async def scenario() -> None:
        async with processor_session() as (session, storage):
            content = huitun_csv([valid_row("atomic-a"), valid_row("atomic-b")])
            job = await seed_import_job(session, storage, content)
            processor = ImportProcessor(session, storage, parser_limits=limits())
            await processor.parse_and_preview(job.id)
            await queue_confirm(session, job.id, 1)
            original_apply = processor._apply_plan
            calls = 0

            async def fail_second(*args: object, **kwargs: object) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("synthetic atomicity failure")
                await original_apply(*args, **kwargs)  # type: ignore[arg-type]

            with (
                patch.object(processor, "_apply_plan", side_effect=fail_second),
                pytest.raises(ImportDomainError) as caught,
            ):
                await processor.confirm(job.id, 1)
            assert caught.value.code == "IMPORT_PROCESSING_FAILED"
            assert "synthetic" not in caught.value.message

            assert await session.scalar(select(func.count()).select_from(Influencer)) == 0
            assert (
                await session.scalar(select(func.count()).select_from(InfluencerPlatformAccount))
                == 0
            )
            refreshed = await session.get(ImportJob, job.id)
            assert refreshed is not None and refreshed.status == ImportJobStatus.FAILED
            assert refreshed.error_code == "IMPORT_PROCESSING_FAILED"
            assert refreshed.failed_stage is ImportJobFailedStage.CONFIRM

    asyncio.run(scenario())


def test_external_source_identity_is_generic_and_keeps_snapshot_keys_distinct() -> None:
    async def scenario() -> None:
        async with processor_session() as (session, storage):
            content = (
                "name,external,followers,updated\n"
                "脱敏甲,source-only-a,100,2026-08-10 12:00:00\n"
                "脱敏乙,source-only-b,100,2026-08-10 12:00:00\n"
            ).encode()
            job = await seed_import_job(
                session,
                storage,
                content,
                source_type=ImportSourceType.GENERIC_CSV,
                field_mapping={
                    "name": "nickname",
                    "external": "external_source_id",
                    "followers": "followers_count",
                    "updated": "source_updated_at",
                },
            )
            processor = ImportProcessor(session, storage, parser_limits=limits())
            preview = await processor.parse_and_preview(job.id)
            assert preview["status"] == ImportJobStatus.PREVIEW_READY.value
            await queue_confirm(session, job.id, 1)
            await processor.confirm(job.id, 1)

            accounts = list(await session.scalars(select(InfluencerPlatformAccount)))
            assert len(accounts) == 2
            assert all(account.platform_account_id is None for account in accounts)
            assert all(account.normalized_profile_url is None for account in accounts)
            identities = list(await session.scalars(select(PlatformAccountSourceIdentity)))
            assert {item.external_account_id for item in identities} == {
                "source-only-a",
                "source-only-b",
            }
            snapshots = list(await session.scalars(select(InfluencerMetricSnapshot)))
            assert len(snapshots) == 2
            assert len({snapshot.snapshot_key for snapshot in snapshots}) == 2

    asyncio.run(scenario())


def test_reimport_observes_existing_source_contact_without_creating_duplicate() -> None:
    async def scenario() -> None:
        async with processor_session() as (session, storage):
            first_content = huitun_csv(
                [valid_row("contact-observation", email="observed@example.com")]
            )
            first_job = await seed_import_job(session, storage, first_content)
            processor = ImportProcessor(session, storage, parser_limits=limits())
            await processor.parse_and_preview(first_job.id)
            await queue_confirm(session, first_job.id, 1)
            await processor.confirm(first_job.id, 1)

            first_row = await session.scalar(
                select(ImportRow).where(ImportRow.import_job_id == first_job.id)
            )
            contact = await session.scalar(select(InfluencerContact))
            assert first_row is not None and contact is not None
            first_seen_at = contact.first_seen_at
            first_last_seen_at = contact.last_seen_at
            first_import_job_id = contact.first_import_job_id
            first_import_row_id = contact.first_import_row_id
            assert contact.last_import_job_id == first_job.id
            assert contact.last_import_row_id == first_row.id

            second_row_data = valid_row("contact-observation", email="observed@example.com")
            second_row_data["更新时间"] = "2026-08-10 12:01:00"
            second_job = await seed_import_job(session, storage, huitun_csv([second_row_data]))
            await processor.parse_and_preview(second_job.id)
            second_row = await session.scalar(
                select(ImportRow).where(ImportRow.import_job_id == second_job.id)
            )
            assert second_row is not None
            assert second_row.action is ImportRowAction.UPDATE
            assert second_row.merge_plan["contacts"]["create"] == []
            assert second_row.merge_plan["contacts"]["observe_ids"] == [str(contact.id)]

            await queue_confirm(session, second_job.id, 1)
            await processor.confirm(second_job.id, 1)
            await session.refresh(contact)

            assert await session.scalar(select(func.count()).select_from(InfluencerContact)) == 1
            assert contact.first_seen_at == first_seen_at
            assert contact.first_import_job_id == first_import_job_id
            assert contact.first_import_row_id == first_import_row_id
            assert contact.last_seen_at >= first_last_seen_at
            assert contact.last_import_job_id == second_job.id
            assert contact.last_import_row_id == second_row.id

    asyncio.run(scenario())


def test_confirm_is_stale_when_contact_observation_changes_after_preview() -> None:
    async def scenario() -> None:
        async with processor_session() as (session, storage):
            first_content = huitun_csv(
                [valid_row("contact-stale", email="stale-contact@example.com")]
            )
            first_job = await seed_import_job(session, storage, first_content)
            processor = ImportProcessor(session, storage, parser_limits=limits())
            await processor.parse_and_preview(first_job.id)
            await queue_confirm(session, first_job.id, 1)
            await processor.confirm(first_job.id, 1)

            second_row_data = valid_row("contact-stale", email="stale-contact@example.com")
            second_row_data["更新时间"] = "2026-08-10 12:01:00"
            second_job = await seed_import_job(session, storage, huitun_csv([second_row_data]))
            await processor.parse_and_preview(second_job.id)

            contact = await session.scalar(select(InfluencerContact))
            assert contact is not None
            contact.last_seen_at = contact.last_seen_at + timedelta(seconds=1)
            await session.commit()
            await queue_confirm(session, second_job.id, 1)

            result = await processor.confirm(second_job.id, 1)

            assert result["status"] == ImportJobStatus.PREVIEW_STALE.value
            refreshed = await session.get(ImportJob, second_job.id)
            assert refreshed is not None
            assert refreshed.status is ImportJobStatus.PREVIEW_STALE
            assert await session.scalar(select(func.count()).select_from(InfluencerContact)) == 1

    asyncio.run(scenario())
