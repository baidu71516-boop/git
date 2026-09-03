"""Real PostgreSQL 16 gates for committed Huitun reprojection."""

from __future__ import annotations

import asyncio
import csv
import io
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
import test_import_postgres as import_gate
from backend_core.db import models as database_models  # noqa: F401
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.hashing import canonical_json, hash_document
from backend_core.imports.huitun_reprojection import HuitunReprojectionService
from backend_core.imports.models import CollectionJob, ImportJob, ImportRow
from backend_core.imports.processor import ImportProcessor
from backend_core.imports.storage import LocalStorageAdapter
from backend_core.influencers.enums import DataSource
from backend_core.influencers.models import (
    Influencer,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
)
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

HUITUN_HEADERS = (
    "播主昵称",
    "抖音号",
    "所属MCN",
    "简介",
    "内容标签",
    "分类",
    "粉丝数",
    "达人主页链接",
    "作品数",
    "点赞数",
)


@dataclass(frozen=True, slots=True)
class HuitunLineage:
    department_id: UUID
    import_job_id: UUID
    import_row_id: UUID
    collection_job_id: UUID
    influencer_id: UUID
    platform_account_id: UUID


def _huitun_csv(profile_token: str) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=HUITUN_HEADERS)
    writer.writeheader()
    writer.writerow(
        {
            "播主昵称": f"舞蹈实测达人-{profile_token}",
            "抖音号": f"dance-handle-{profile_token}",
            "所属MCN": "真实 PostgreSQL 测试 MCN",
            "简介": "Huitun committed import reprojection gate",
            "内容标签": "舞蹈,街舞",
            "分类": "舞蹈",
            "粉丝数": "871798.0",
            "达人主页链接": f"https://www.douyin.com/user/{profile_token}",
            "作品数": "53.0",
            "点赞数": "6228903.0",
        }
    )
    return stream.getvalue().encode("utf-8-sig")


def _semantic_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _snapshot_persisted_fields(snapshot: InfluencerMetricSnapshot) -> dict[str, object]:
    """Capture every persisted snapshot field, including immutable lineage."""

    return {
        "id": snapshot.id,
        "created_at": _as_utc(snapshot.created_at),
        "updated_at": _as_utc(snapshot.updated_at),
        "source": snapshot.source,
        "influencer_id": snapshot.influencer_id,
        "platform_account_id": snapshot.platform_account_id,
        "import_job_id": snapshot.import_job_id,
        "import_row_id": snapshot.import_row_id,
        "captured_at": _as_utc(snapshot.captured_at),
        "source_updated_at": (
            _as_utc(snapshot.source_updated_at) if snapshot.source_updated_at is not None else None
        ),
        "metrics": _semantic_bytes(snapshot.metrics),
        "metrics_hash": snapshot.metrics_hash,
        "snapshot_key": snapshot.snapshot_key,
    }


def _persisted_fields(model: Any) -> tuple[tuple[str, str], ...]:
    return tuple(
        (column.name, canonical_json(getattr(model, column.name)))
        for column in model.__table__.columns
    )


async def _count(session: AsyncSession, model: type[object]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _assert_postgresql_16(session: AsyncSession) -> str:
    version = str(await session.scalar(text("SHOW server_version")))
    version_number = int(await session.scalar(text("SHOW server_version_num")))
    print(f"HUITUN_REPROJECTION_POSTGRES_VERSION {version} ({version_number})")
    assert version_number // 10_000 == 16
    assert version.startswith("16.")
    return version


async def _seed_committed_huitun(
    factory: async_sessionmaker[AsyncSession],
    storage: LocalStorageAdapter,
    profile_token: str,
) -> HuitunLineage:
    async with factory() as session:
        job_id = (
            await import_gate._seed_preview_ready_jobs(
                session,
                storage,
                profile_id=profile_token,
                job_count=1,
                content=_huitun_csv(profile_token),
            )
        )[0]
        queued_job = await session.get(ImportJob, job_id)
        assert queued_job is not None
        queued_job.confirmed_at = datetime.now(UTC)
        await session.commit()
        result = await ImportProcessor(
            session,
            storage,
            parser_limits=import_gate._limits(),
        ).confirm(job_id, 1)
        assert result["created_rows"] == 1

        job = await session.get(ImportJob, job_id)
        row = await session.scalar(select(ImportRow).where(ImportRow.import_job_id == job_id))
        assert job is not None and row is not None
        assert row.matched_influencer_id is not None
        assert row.matched_platform_account_id is not None
        assert job.status.value == "completed"
        assert job.confirmed_revision == 1
        return HuitunLineage(
            department_id=job.department_id,
            import_job_id=job.id,
            import_row_id=row.id,
            collection_job_id=job.collection_job_id,
            influencer_id=row.matched_influencer_id,
            platform_account_id=row.matched_platform_account_id,
        )


async def _projection_snapshot(
    session: AsyncSession,
    lineage: HuitunLineage,
) -> dict[str, object]:
    row = await session.get(ImportRow, lineage.import_row_id)
    job = await session.get(ImportJob, lineage.import_job_id)
    collection = await session.get(CollectionJob, lineage.collection_job_id)
    influencer = await session.get(Influencer, lineage.influencer_id)
    account = await session.get(InfluencerPlatformAccount, lineage.platform_account_id)
    state = await session.scalar(
        select(InfluencerSourceState).where(
            InfluencerSourceState.platform_account_id == lineage.platform_account_id,
            InfluencerSourceState.source == DataSource.HUITUN,
        )
    )
    current = await session.scalar(
        select(InfluencerCurrentMetrics).where(
            InfluencerCurrentMetrics.platform_account_id == lineage.platform_account_id,
            InfluencerCurrentMetrics.source == DataSource.HUITUN,
        )
    )
    snapshots = list(
        await session.scalars(
            select(InfluencerMetricSnapshot)
            .where(InfluencerMetricSnapshot.import_job_id == lineage.import_job_id)
            .order_by(InfluencerMetricSnapshot.id)
        )
    )
    assert (
        row is not None
        and job is not None
        and collection is not None
        and influencer is not None
        and account is not None
    )
    return {
        "job": _persisted_fields(job),
        "row": _persisted_fields(row),
        "raw_data_bytes": _semantic_bytes(row.raw_data),
        "collection": _persisted_fields(collection),
        "influencer": _persisted_fields(influencer),
        "account": _persisted_fields(account),
        "source_state": _persisted_fields(state) if state is not None else None,
        "current_metrics": _persisted_fields(current) if current is not None else None,
        "snapshots": tuple(_snapshot_persisted_fields(item) for item in snapshots),
        "counts": (
            await _count(session, Influencer),
            await _count(session, InfluencerPlatformAccount),
            await _count(session, InfluencerSourceState),
            await _count(session, InfluencerCurrentMetrics),
            await _count(session, InfluencerMetricSnapshot),
        ),
    }


@contextmanager
def _capture_snapshot_dml(session: AsyncSession) -> Iterator[list[str]]:
    statements: list[str] = []
    engine = session.sync_session.get_bind()

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        normalized = statement.lstrip().upper()
        if normalized.startswith(("INSERT", "UPDATE", "DELETE")) and (
            "INFLUENCER_METRIC_SNAPSHOTS" in normalized
        ):
            statements.append(normalized)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_postgresql_huitun_reprojection_repairs_history_and_replays_idempotently() -> None:
    async def scenario() -> None:
        async with import_gate._isolated_postgres() as (factory, storage):
            async with factory() as session:
                await _assert_postgresql_16(session)

            lineage = await _seed_committed_huitun(factory, storage, "reprojection-repair")

            async with factory() as setup_session:
                row = await setup_session.get(ImportRow, lineage.import_row_id)
                job = await setup_session.get(ImportJob, lineage.import_job_id)
                collection = await setup_session.get(CollectionJob, lineage.collection_job_id)
                state = await setup_session.scalar(
                    select(InfluencerSourceState).where(
                        InfluencerSourceState.platform_account_id == lineage.platform_account_id,
                        InfluencerSourceState.source == DataSource.HUITUN,
                    )
                )
                snapshot = await setup_session.scalar(
                    select(InfluencerMetricSnapshot).where(
                        InfluencerMetricSnapshot.import_row_id == lineage.import_row_id
                    )
                )
                assert row is not None and job is not None and collection is not None
                assert state is not None and snapshot is not None
                assert row.raw_data["分类"] == "舞蹈"
                assert row.raw_data["粉丝数"] == "871798.0"
                assert row.raw_data["作品数"] == "53.0"
                assert row.raw_data["点赞数"] == "6228903.0"
                assert state.source_data["creator_classification_tags"] == ["舞蹈"]
                assert row.committed_at is not None
                original_committed_at = row.committed_at
                raw_before = dict(row.raw_data)
                raw_before_bytes = _semantic_bytes(row.raw_data)
                collection_before = (collection.industry, collection.subdirection)

                repaired_source_data = dict(state.source_data)
                repaired_source_data.pop("creator_classification_tags")
                state.source_data = repaired_source_data
                state.source_data_hash = hash_document(repaired_source_data)
                await setup_session.delete(snapshot)
                await setup_session.commit()

            async with factory() as repair_session:
                result = await HuitunReprojectionService(repair_session).reproject(
                    department_id=lineage.department_id,
                    import_job_id=lineage.import_job_id,
                )
                assert result == {
                    "department_id": str(lineage.department_id),
                    "import_job_id": str(lineage.import_job_id),
                    "selected_committed_rows": 1,
                    "processed": 1,
                    "source_states_repaired": 1,
                    "source_states_noop": 0,
                    "metric_snapshots_inserted": 1,
                    "metric_snapshots_noop": 0,
                    "conflicts": 0,
                    "errors": 0,
                }

            async with factory() as inspection_session:
                after_first = await _projection_snapshot(inspection_session, lineage)
                row = await inspection_session.get(ImportRow, lineage.import_row_id)
                job = await inspection_session.get(ImportJob, lineage.import_job_id)
                collection = await inspection_session.get(CollectionJob, lineage.collection_job_id)
                influencer = await inspection_session.get(Influencer, lineage.influencer_id)
                account = await inspection_session.get(
                    InfluencerPlatformAccount, lineage.platform_account_id
                )
                state = await inspection_session.scalar(
                    select(InfluencerSourceState).where(
                        InfluencerSourceState.platform_account_id == lineage.platform_account_id,
                        InfluencerSourceState.source == DataSource.HUITUN,
                    )
                )
                current = await inspection_session.scalar(
                    select(InfluencerCurrentMetrics).where(
                        InfluencerCurrentMetrics.platform_account_id == lineage.platform_account_id,
                        InfluencerCurrentMetrics.source == DataSource.HUITUN,
                    )
                )
                snapshots = list(
                    await inspection_session.scalars(
                        select(InfluencerMetricSnapshot).where(
                            InfluencerMetricSnapshot.import_job_id == lineage.import_job_id,
                            InfluencerMetricSnapshot.import_row_id == lineage.import_row_id,
                        )
                    )
                )
                assert (
                    row is not None
                    and job is not None
                    and collection is not None
                    and influencer is not None
                    and account is not None
                    and state is not None
                    and current is not None
                )
                assert row.raw_data == raw_before
                assert _semantic_bytes(row.raw_data) == raw_before_bytes
                assert row.matched_influencer_id == lineage.influencer_id
                assert row.matched_platform_account_id == lineage.platform_account_id
                assert influencer.id == lineage.influencer_id
                assert account.id == lineage.platform_account_id
                assert account.influencer_id == lineage.influencer_id
                assert state.source_data["creator_classification_tags"] == ["舞蹈"]
                assert current.metrics["followers_count"] == 871798
                assert len(snapshots) == 1
                snapshot = snapshots[0]
                assert snapshot.source is DataSource.HUITUN
                assert snapshot.import_job_id == lineage.import_job_id
                assert snapshot.import_row_id == lineage.import_row_id
                assert snapshot.influencer_id == lineage.influencer_id
                assert snapshot.platform_account_id == lineage.platform_account_id
                assert snapshot.metrics["followers_count"] == 871798
                assert snapshot.captured_at == original_committed_at
                assert (collection.industry, collection.subdirection) == collection_before
                assert await _count(inspection_session, Influencer) == 1
                assert await _count(inspection_session, InfluencerPlatformAccount) == 1
                assert await _count(inspection_session, InfluencerMetricSnapshot) == 1

            async with factory() as replay_session:
                before_replay = await _projection_snapshot(replay_session, lineage)
                replay_result = await HuitunReprojectionService(replay_session).reproject(
                    department_id=lineage.department_id,
                    import_job_id=lineage.import_job_id,
                )
                assert replay_result == {
                    "department_id": str(lineage.department_id),
                    "import_job_id": str(lineage.import_job_id),
                    "selected_committed_rows": 1,
                    "processed": 1,
                    "source_states_repaired": 0,
                    "source_states_noop": 1,
                    "metric_snapshots_inserted": 0,
                    "metric_snapshots_noop": 1,
                    "conflicts": 0,
                    "errors": 0,
                }

            async with factory() as final_session:
                after_replay = await _projection_snapshot(final_session, lineage)
            assert after_replay == before_replay
            assert after_replay == after_first

    asyncio.run(scenario())


def test_postgresql_huitun_reprojection_conflict_rolls_back_immutable_snapshot() -> None:
    async def scenario() -> None:
        async with import_gate._isolated_postgres() as (factory, storage):
            async with factory() as session:
                await _assert_postgresql_16(session)

            lineage = await _seed_committed_huitun(factory, storage, "reprojection-conflict")

            async with factory() as setup_session:
                state = await setup_session.scalar(
                    select(InfluencerSourceState).where(
                        InfluencerSourceState.platform_account_id == lineage.platform_account_id,
                        InfluencerSourceState.source == DataSource.HUITUN,
                    )
                )
                snapshot = await setup_session.scalar(
                    select(InfluencerMetricSnapshot).where(
                        InfluencerMetricSnapshot.import_row_id == lineage.import_row_id
                    )
                )
                assert state is not None and snapshot is not None
                historical_source_data = dict(state.source_data)
                historical_source_data.pop("creator_classification_tags")
                state.source_data = historical_source_data
                state.source_data_hash = hash_document(historical_source_data)
                snapshot.metrics = {"followers_count": 1}
                snapshot.metrics_hash = hash_document(snapshot.metrics)
                snapshot.snapshot_key = "d" * 64
                await setup_session.commit()

            async with factory() as before_session:
                before_state = await _projection_snapshot(before_session, lineage)
                snapshot = await before_session.scalar(
                    select(InfluencerMetricSnapshot).where(
                        InfluencerMetricSnapshot.import_row_id == lineage.import_row_id
                    )
                )
                assert snapshot is not None
                snapshot_before = _snapshot_persisted_fields(snapshot)

            async with factory() as call_session:
                with _capture_snapshot_dml(call_session) as snapshot_dml:
                    with pytest.raises(ImportDomainError) as error:
                        await HuitunReprojectionService(call_session).reproject(
                            department_id=lineage.department_id,
                            import_job_id=lineage.import_job_id,
                        )
                assert error.value.code == "HUITUN_REPROJECTION_SNAPSHOT_CONFLICT"
                assert snapshot_dml == []

            async with factory() as after_session:
                after_state = await _projection_snapshot(after_session, lineage)
                restored = await after_session.scalar(
                    select(InfluencerMetricSnapshot).where(
                        InfluencerMetricSnapshot.import_row_id == lineage.import_row_id
                    )
                )
                assert restored is not None
                assert _snapshot_persisted_fields(restored) == snapshot_before
                assert await _count(after_session, InfluencerMetricSnapshot) == 1
                assert await _count(after_session, Influencer) == 1
                assert await _count(after_session, InfluencerPlatformAccount) == 1

            assert after_state == before_state

    asyncio.run(scenario())
