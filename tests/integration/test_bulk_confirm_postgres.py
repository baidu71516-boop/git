"""Real PostgreSQL 16 gates for atomic Phase 2 Bulk Confirm."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import test_unified_preview_postgres as preview_gate
from backend_core.audit.enums import AuditAction, AuditResult
from backend_core.audit.models import AuditLog
from backend_core.config import Settings
from backend_core.imports.confirm_processor import BulkConfirmProcessor
from backend_core.imports.enums import (
    ImportJobFileStatus,
    ImportJobStatus,
    ImportRowAction,
    ImportTaskKind,
    ImportTaskState,
    SourceAcquiredAtOrigin,
)
from backend_core.imports.errors import ImportDomainError
from backend_core.imports.hashing import hash_document
from backend_core.imports.merge_applier import ImportMergeApplier
from backend_core.imports.models import (
    CollectionJob,
    ImportJob,
    ImportJobFile,
    ImportTaskRequest,
    StoredImportFile,
)
from backend_core.imports.task_service import ClaimStatus, ImportTaskService, TaskEnvelope
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    DataSource,
)
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


def _settings() -> Settings:
    return Settings(app_env="test", database_url=str(preview_gate.TEST_DATABASE_URL))


async def _business_and_lineage_snapshot(
    session: AsyncSession,
    job_id: UUID,
) -> dict[str, object]:
    """Capture stable facts whose cardinality must survive completed replay."""

    rows = await preview_gate._job_rows(session, job_id)
    counts: list[int] = []
    for model in (
        Influencer,
        InfluencerPlatformAccount,
        PlatformAccountSourceIdentity,
        InfluencerSourceState,
        InfluencerContact,
        InfluencerCurrentMetrics,
        InfluencerMetricSnapshot,
    ):
        counts.append(int(await session.scalar(select(func.count()).select_from(model)) or 0))
    return {
        "counts": tuple(counts),
        "snapshots": tuple(
            sorted(
                (
                    item.id,
                    item.platform_account_id,
                    item.snapshot_key,
                    item.metrics_hash,
                )
                for item in await session.scalars(select(InfluencerMetricSnapshot))
            )
        ),
        "lineage": tuple(
            (
                row.id,
                row.action,
                row.committed_action,
                row.matched_influencer_id,
                row.matched_platform_account_id,
                row.committed_at,
            )
            for row in rows
        ),
    }


async def _claim_confirm(
    harness: preview_gate.PostgresHarness,
    job_id: UUID,
    revision: int,
) -> tuple[UUID, int]:
    async with harness.factory() as session:
        job = await session.get(ImportJob, job_id, with_for_update=True)
        assert job is not None
        service = ImportTaskService(session, _settings())
        task = await service.find_active(
            task_kind=ImportTaskKind.CONFIRM,
            import_job_id=job_id,
            preview_revision=revision,
            for_update=True,
        )
        assert task is not None
        if task.dispatch_attempts == 0:
            dispatch = await service.prepare_dispatch(task.task_token)
            assert dispatch.envelope is not None
        envelope = TaskEnvelope.from_task(task)
        await session.commit()
    async with harness.factory() as session:
        claim = await ImportTaskService(session, _settings()).claim(envelope)
        assert claim.generation is not None
        await session.commit()
        return task.task_token, claim.generation


async def _preview(
    harness: preview_gate.PostgresHarness,
    seeded: preview_gate.SeededBatch,
) -> int:
    task_id = str(uuid4())
    decision = await preview_gate._request_preview(harness, seeded, task_id)
    token = decision[2]
    # The Task 5 helper invokes the processor directly; finish its durable task
    # explicitly after the successful business commit for this compatibility
    # fixture. Dedicated recovery tests exercise atomic worker completion.
    await preview_gate._build(harness, seeded.job_id, token)
    async with harness.factory() as session:
        task = await session.scalar(
            select(ImportTaskRequest).where(ImportTaskRequest.task_token == UUID(token))
        )
        job = await session.get(ImportJob, seeded.job_id)
        assert task is not None and job is not None
        # Direct Preview helper did not claim. Turn the historical fixture task
        # terminal so a later rebuild is legal; Confirm itself always uses claim.
        task.state = ImportTaskState.COMPLETED
        task.completed_at = datetime.now(UTC)
        await session.commit()
        return job.preview_revision


async def _request_confirm(
    harness: preview_gate.PostgresHarness,
    seeded: preview_gate.SeededBatch,
    revision: int,
) -> UUID:
    async with harness.factory() as session:
        decision = await preview_gate._service(session, harness.storage).request_confirm(
            await preview_gate._context(session, seeded),
            seeded.job_id,
            revision,
            str(uuid4()),
            ip="127.0.0.1",
            user_agent="Task6 PostgreSQL integration",
        )
        return UUID(decision.task_id)


async def _run_confirm(
    harness: preview_gate.PostgresHarness,
    seeded: preview_gate.SeededBatch,
    revision: int,
) -> dict[str, object]:
    await _request_confirm(harness, seeded, revision)
    token, generation = await _claim_confirm(harness, seeded.job_id, revision)
    async with harness.factory() as session:
        return await BulkConfirmProcessor(
            session,
            harness.storage,
            parser_limits=preview_gate.ParserLimits(
                max_rows=10_000,
                max_columns=100,
                max_cells=1_000_000,
            ),
            settings=_settings(),
            max_batch_rows=10_000,
        ).confirm(seeded.job_id, revision, token, generation)


async def _run_claimed_confirm(
    harness: preview_gate.PostgresHarness,
    seeded: preview_gate.SeededBatch,
    revision: int,
    token: UUID,
    generation: int,
    barrier: asyncio.Barrier | None = None,
) -> dict[str, object]:
    async with harness.factory() as session:
        if barrier is not None:
            await barrier.wait()
        return await BulkConfirmProcessor(
            session,
            harness.storage,
            parser_limits=preview_gate.ParserLimits(
                max_rows=10_000,
                max_columns=100,
                max_cells=1_000_000,
            ),
            settings=_settings(),
            max_batch_rows=10_000,
        ).confirm(seeded.job_id, revision, token, generation)


async def _repeat_same_batch(
    harness: preview_gate.PostgresHarness,
    original: preview_gate.SeededBatch,
) -> preview_gate.SeededBatch:
    async with harness.factory() as session:
        original_job = await session.get(ImportJob, original.job_id)
        assert original_job is not None
        job = ImportJob(
            collection_job_id=original_job.collection_job_id,
            department_id=original_job.department_id,
            operator_id=original_job.operator_id,
            source_type=original_job.source_type,
            status=ImportJobStatus.DRAFT,
            preview_revision=0,
        )
        session.add(job)
        await session.flush()
        files: list[ImportJobFile] = []
        for source in original.files:
            occurrence = ImportJobFile(
                import_job_id=job.id,
                stored_file_id=source.stored_file_id,
                position=source.position,
                original_filename=source.original_filename,
                declared_mime=source.declared_mime,
                status=ImportJobFileStatus.PARSING,
                source_acquired_at=source.source_acquired_at,
                source_acquired_at_origin=SourceAcquiredAtOrigin.USER_CONFIRMED,
                source_acquired_at_confirmation_required=False,
                field_mapping=dict(source.field_mapping or {}),
                parse_task_id=f"repeat-parse-{job.id}-{source.position}",
                parse_attempts=1,
            )
            session.add(occurrence)
            files.append(occurrence)
        await session.commit()
        return preview_gate.SeededBatch(
            job.id,
            original.department_id,
            original.operator_id,
            original.auth_session_id,
            tuple(files),
        )


def test_bulk_confirm_atomic_merge_and_completed_replay() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            seeded = await preview_gate._seed_batch(
                harness,
                [[preview_gate._row(f"confirm-{index}") for index in range(20)]],
            )
            await preview_gate._parse_all(harness, seeded)
            revision = await _preview(harness, seeded)
            await _request_confirm(harness, seeded, revision)
            token, generation = await _claim_confirm(harness, seeded.job_id, revision)
            result = await _run_claimed_confirm(
                harness,
                seeded,
                revision,
                token,
                generation,
            )
            assert result.get("created_rows") == 20, result
            async with harness.factory() as session:
                job = await session.get(ImportJob, seeded.job_id)
                assert job is not None and job.status is ImportJobStatus.COMPLETED
                task = await session.scalar(
                    select(ImportTaskRequest).where(
                        ImportTaskRequest.import_job_id == job.id,
                        ImportTaskRequest.task_kind == ImportTaskKind.CONFIRM,
                    )
                )
                assert task is not None
                assert task.task_token == token
                assert task.state is ImportTaskState.COMPLETED
                assert task.completed_at is not None
                assert task.lease_expires_at is None
                rows = await preview_gate._job_rows(session, job.id)
                assert all(row.committed_at is not None for row in rows)
                assert all(row.matched_platform_account_id is not None for row in rows)
                assert all(row.committed_action is ImportRowAction.CREATE for row in rows)
                audits = list(
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.entity_id == job.id,
                            AuditLog.action == AuditAction.IMPORT_BATCH_COMPLETED,
                        )
                    )
                )
                assert len(audits) == 1
                assert audits[0].result is AuditResult.SUCCESS
                assert audits[0].after is not None
                assert audits[0].after["preview_revision"] == revision
                envelope = TaskEnvelope.from_task(task)
                before_replay = await _business_and_lineage_snapshot(session, job.id)

            # Simulate the DB-commit-before-Broker-ACK crash: a real duplicate
            # delivery claims the same durable Bulk task and must short-circuit.
            async with harness.factory() as session:
                replay = await ImportTaskService(session, _settings()).claim(envelope)
                assert replay.status is ClaimStatus.COMPLETED
                assert replay.generation is None
                await session.commit()
            async with harness.factory() as session:
                replayed_job = await session.get(ImportJob, seeded.job_id)
                assert replayed_job is not None and replayed_job.result == result
                assert (
                    await _business_and_lineage_snapshot(session, replayed_job.id) == before_replay
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(
                            AuditLog.entity_id == replayed_job.id,
                            AuditLog.action == AuditAction.IMPORT_BATCH_COMPLETED,
                        )
                    )
                    == 1
                )

    asyncio.run(scenario())


def test_bulk_confirm_plan_change_marks_entire_revision_stale() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            seeded = await preview_gate._seed_batch(
                harness,
                [[preview_gate._row(f"stale-{index}") for index in range(5)]],
            )
            await preview_gate._parse_all(harness, seeded)
            revision = await _preview(harness, seeded)
            await _request_confirm(harness, seeded, revision)
            token, generation = await _claim_confirm(harness, seeded.job_id, revision)
            async with harness.factory() as session:
                row = (await preview_gate._job_rows(session, seeded.job_id))[0]
                row.plan_hash = "f" * 64
                await session.commit()
            async with harness.factory() as session:
                result = await BulkConfirmProcessor(
                    session,
                    harness.storage,
                    parser_limits=preview_gate.ParserLimits(
                        max_rows=10_000, max_columns=100, max_cells=1_000_000
                    ),
                    settings=_settings(),
                ).confirm(seeded.job_id, revision, token, generation)
                assert result["status"] == ImportJobStatus.PREVIEW_STALE.value
            async with harness.factory() as session:
                job = await session.get(ImportJob, seeded.job_id)
                assert job is not None and job.status is ImportJobStatus.PREVIEW_STALE
                task = await session.scalar(
                    select(ImportTaskRequest).where(ImportTaskRequest.task_token == token)
                )
                assert task is not None
                assert task.state is ImportTaskState.TERMINAL_FAILED
                assert task.completed_at is not None
                assert task.lease_expires_at is None
                rows = await preview_gate._job_rows(session, job.id)
                assert all(row.committed_at is None for row in rows)
                audits = list(
                    await session.scalars(
                        select(AuditLog).where(
                            AuditLog.entity_id == job.id,
                            AuditLog.action == AuditAction.IMPORT_PREVIEW_STALE,
                        )
                    )
                )
                assert len(audits) == 1
                assert audits[0].result is AuditResult.DENIED
                assert audits[0].after is not None
                assert audits[0].after["preview_revision"] == revision

    asyncio.run(scenario())


@pytest.mark.parametrize("drift", ["mapping", "screening", "storage_sha"])
def test_preview_relevant_configuration_and_storage_drift_is_atomic_stale(
    drift: str,
) -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            seeded = await preview_gate._seed_batch(
                harness,
                [[preview_gate._row(f"{drift}-{index}") for index in range(3)]],
            )
            await preview_gate._parse_all(harness, seeded)
            revision = await _preview(harness, seeded)
            await _request_confirm(harness, seeded, revision)
            token, generation = await _claim_confirm(harness, seeded.job_id, revision)

            if drift == "storage_sha":
                async with harness.factory() as session:
                    occurrence = await session.get(ImportJobFile, seeded.files[0].id)
                    assert occurrence is not None
                    stored = await session.get(StoredImportFile, occurrence.stored_file_id)
                    assert stored is not None
                    storage_key = stored.storage_key
                path = harness.storage.root / storage_key
                content = path.read_bytes()
                assert content
                replacement = b"X" if content[-1:] != b"X" else b"Y"
                path.write_bytes(content[:-1] + replacement)
            else:
                async with harness.factory() as session:
                    job = await session.get(ImportJob, seeded.job_id)
                    assert job is not None
                    if drift == "mapping":
                        occurrence = await session.get(ImportJobFile, seeded.files[0].id)
                        assert occurrence is not None and occurrence.field_mapping is not None
                        occurrence.field_mapping = {
                            **occurrence.field_mapping,
                            "fixture_nonce": "bio",
                        }
                    else:
                        collection = await session.get(CollectionJob, job.collection_job_id)
                        assert collection is not None
                        collection.screening_rules = {
                            "schema_version": 1,
                            "platforms": [],
                            "source_tags_exact_any": ["different-rule"],
                        }
                        collection.screening_rules_revision += 1
                    await session.commit()

            result = await _run_claimed_confirm(
                harness,
                seeded,
                revision,
                token,
                generation,
            )
            assert result == {
                "import_job_id": str(seeded.job_id),
                "status": ImportJobStatus.PREVIEW_STALE.value,
                "error_code": "PREVIEW_STALE",
            }
            async with harness.factory() as session:
                job = await session.get(ImportJob, seeded.job_id)
                task = await session.scalar(
                    select(ImportTaskRequest).where(ImportTaskRequest.task_token == token)
                )
                assert job is not None and job.status is ImportJobStatus.PREVIEW_STALE
                assert task is not None and task.state is ImportTaskState.TERMINAL_FAILED
                assert task.completed_at is not None
                assert await session.scalar(select(func.count()).select_from(Influencer)) == 0
                rows = await preview_gate._job_rows(session, job.id)
                assert all(row.committed_at is None for row in rows)
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(
                            AuditLog.entity_id == job.id,
                            AuditLog.action == AuditAction.IMPORT_PREVIEW_STALE,
                            AuditLog.result == AuditResult.DENIED,
                        )
                    )
                    == 1
                )

    asyncio.run(scenario())


def test_mapping_and_hash_synchronized_to_incompatible_contract_is_atomic_stale() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            seeded = await preview_gate._seed_batch(
                harness,
                [[preview_gate._row(f"mapping-contract-{index}") for index in range(3)]],
            )
            await preview_gate._parse_all(harness, seeded)
            revision = await _preview(harness, seeded)
            await _request_confirm(harness, seeded, revision)
            token, generation = await _claim_confirm(harness, seeded.job_id, revision)

            incompatible_mapping = {
                "name": "nickname",
                "email": "email",
            }
            async with harness.factory() as session:
                occurrence = await session.get(ImportJobFile, seeded.files[0].id)
                assert occurrence is not None
                occurrence.field_mapping = incompatible_mapping
                occurrence.mapping_hash = hash_document(incompatible_mapping)
                await session.commit()

            result = await _run_claimed_confirm(
                harness,
                seeded,
                revision,
                token,
                generation,
            )
            assert result == {
                "import_job_id": str(seeded.job_id),
                "status": ImportJobStatus.PREVIEW_STALE.value,
                "error_code": "PREVIEW_STALE",
            }
            async with harness.factory() as session:
                job = await session.get(ImportJob, seeded.job_id)
                task = await session.scalar(
                    select(ImportTaskRequest).where(ImportTaskRequest.task_token == token)
                )
                assert job is not None and job.status is ImportJobStatus.PREVIEW_STALE
                assert task is not None and task.state is ImportTaskState.TERMINAL_FAILED
                assert task.completed_at is not None
                assert await session.scalar(select(func.count()).select_from(Influencer)) == 0
                assert (
                    await session.scalar(
                        select(func.count()).select_from(InfluencerPlatformAccount)
                    )
                    == 0
                )
                assert (
                    await session.scalar(select(func.count()).select_from(InfluencerMetricSnapshot))
                    == 0
                )
                rows = await preview_gate._job_rows(session, job.id)
                assert all(row.committed_at is None for row in rows)
                assert all(row.committed_action is None for row in rows)
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(
                            AuditLog.entity_id == job.id,
                            AuditLog.action == AuditAction.IMPORT_PREVIEW_STALE,
                            AuditLog.result == AuditResult.DENIED,
                        )
                    )
                    == 1
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("payload_field", ["merge_plan", "warnings"])
def test_frozen_row_payload_tamper_without_plan_hash_change_is_atomic_stale(
    payload_field: str,
) -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            seeded = await preview_gate._seed_batch(
                harness,
                [[preview_gate._row(f"payload-tamper-{index}") for index in range(3)]],
            )
            await preview_gate._parse_all(harness, seeded)
            revision = await _preview(harness, seeded)
            await _request_confirm(harness, seeded, revision)
            token, generation = await _claim_confirm(harness, seeded.job_id, revision)

            async with harness.factory() as session:
                row = (await preview_gate._job_rows(session, seeded.job_id))[0]
                original_plan_hash = row.plan_hash
                if payload_field == "merge_plan":
                    row.merge_plan = {**row.merge_plan, "tampered_payload": True}
                else:
                    row.warnings = [
                        *row.warnings,
                        {
                            "code": "TAMPERED_WARNING",
                            "message": "synthetic frozen payload tamper",
                        },
                    ]
                assert row.plan_hash == original_plan_hash
                await session.commit()

            result = await _run_claimed_confirm(
                harness,
                seeded,
                revision,
                token,
                generation,
            )
            assert result == {
                "import_job_id": str(seeded.job_id),
                "status": ImportJobStatus.PREVIEW_STALE.value,
                "error_code": "PREVIEW_STALE",
            }
            async with harness.factory() as session:
                job = await session.get(ImportJob, seeded.job_id)
                task = await session.scalar(
                    select(ImportTaskRequest).where(ImportTaskRequest.task_token == token)
                )
                assert job is not None and job.status is ImportJobStatus.PREVIEW_STALE
                assert task is not None and task.state is ImportTaskState.TERMINAL_FAILED
                assert task.completed_at is not None
                assert await session.scalar(select(func.count()).select_from(Influencer)) == 0
                assert (
                    await session.scalar(
                        select(func.count()).select_from(InfluencerPlatformAccount)
                    )
                    == 0
                )
                assert (
                    await session.scalar(select(func.count()).select_from(InfluencerMetricSnapshot))
                    == 0
                )
                rows = await preview_gate._job_rows(session, job.id)
                assert all(row.committed_at is None for row in rows)
                assert all(row.committed_action is None for row in rows)
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(
                            AuditLog.entity_id == job.id,
                            AuditLog.action == AuditAction.IMPORT_PREVIEW_STALE,
                            AuditLog.result == AuditResult.DENIED,
                        )
                    )
                    == 1
                )

    asyncio.run(scenario())


def test_four_by_500_atomic_confirm_performance_and_entity_counts() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            seeded = await preview_gate._seed_batch(
                harness, preview_gate._matrix_files(), screening=True
            )
            await preview_gate._parse_all(harness, seeded)
            await preview_gate._seed_matrix_database_state(harness, seeded)
            revision = await _preview(harness, seeded)
            await _request_confirm(harness, seeded, revision)
            token, generation = await _claim_confirm(harness, seeded.job_id, revision)
            async with harness.factory() as session:
                preview_rows = await preview_gate._job_rows(session, seeded.job_id)
                non_merge_preview_lineage = {
                    row.id: (
                        row.matched_influencer_id,
                        row.matched_platform_account_id,
                    )
                    for row in preview_rows
                    if row.action
                    in {
                        ImportRowAction.MANUAL_REVIEW,
                        ImportRowAction.ERROR,
                        ImportRowAction.SKIP,
                    }
                }

            harness.probe.start()
            async with harness.factory() as session:
                result = await BulkConfirmProcessor(
                    session,
                    harness.storage,
                    parser_limits=preview_gate.ParserLimits(
                        max_rows=10_000,
                        max_columns=100,
                        max_cells=1_000_000,
                    ),
                    settings=_settings(),
                    max_batch_rows=10_000,
                ).confirm(seeded.job_id, revision, token, generation)
            measurement = harness.probe.stop(rows=2_000)

            async with harness.factory() as session:
                model_counts = {
                    "influencers": int(
                        await session.scalar(select(func.count()).select_from(Influencer)) or 0
                    ),
                    "accounts": int(
                        await session.scalar(
                            select(func.count()).select_from(InfluencerPlatformAccount)
                        )
                        or 0
                    ),
                    "source_identities": int(
                        await session.scalar(
                            select(func.count()).select_from(PlatformAccountSourceIdentity)
                        )
                        or 0
                    ),
                    "source_states": int(
                        await session.scalar(
                            select(func.count()).select_from(InfluencerSourceState)
                        )
                        or 0
                    ),
                    "contacts": int(
                        await session.scalar(select(func.count()).select_from(InfluencerContact))
                        or 0
                    ),
                    "current_metrics": int(
                        await session.scalar(
                            select(func.count()).select_from(InfluencerCurrentMetrics)
                        )
                        or 0
                    ),
                    "snapshots": int(
                        await session.scalar(
                            select(func.count()).select_from(InfluencerMetricSnapshot)
                        )
                        or 0
                    ),
                }
                rows = await preview_gate._job_rows(session, seeded.job_id)
                assert len(rows) == 2_000
                assert all(row.committed_at is not None for row in rows)
                assert all(row.committed_action is row.action for row in rows)
                action_counts = {
                    action: sum(row.action is action for row in rows) for action in ImportRowAction
                }
                assert action_counts == {
                    ImportRowAction.CREATE: 1_994,
                    ImportRowAction.UPDATE: 1,
                    ImportRowAction.NO_CHANGE: 1,
                    ImportRowAction.SKIP: 2,
                    ImportRowAction.ERROR: 1,
                    ImportRowAction.MANUAL_REVIEW: 1,
                }
                for row in rows:
                    if row.action in {
                        ImportRowAction.CREATE,
                        ImportRowAction.UPDATE,
                        ImportRowAction.NO_CHANGE,
                    }:
                        assert row.matched_influencer_id is not None
                        assert row.matched_platform_account_id is not None
                    else:
                        assert (
                            row.matched_influencer_id,
                            row.matched_platform_account_id,
                        ) == non_merge_preview_lineage[row.id]
                task = await session.scalar(
                    select(ImportTaskRequest).where(ImportTaskRequest.task_token == token)
                )
                assert task is not None and task.state is ImportTaskState.COMPLETED
                assert task.completed_at is not None
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(
                            AuditLog.entity_id == seeded.job_id,
                            AuditLog.action == AuditAction.IMPORT_BATCH_COMPLETED,
                            AuditLog.result == AuditResult.SUCCESS,
                        )
                    )
                    == 1
                )

            print(
                "TASK6_CONFIRM_SQL "
                + json.dumps(
                    {
                        "rows": 2_000,
                        "total": measurement.total,
                        "select": measurement.selects,
                        "dml": measurement.dml,
                        "advisory": measurement.advisory,
                        "wall_seconds": round(measurement.wall_seconds, 6),
                        "rss_high_water_delta_bytes": measurement.rss_high_water_delta_bytes,
                        "rss_high_water_after_bytes": measurement.rss_high_water_after_bytes,
                        "entity_counts": model_counts,
                    },
                    sort_keys=True,
                )
            )
            assert result == {
                "import_job_id": str(seeded.job_id),
                "preview_revision": 1,
                "created_rows": 1_994,
                "updated_rows": 1,
                "no_change_rows": 1,
                "skipped_rows": 2,
                "error_rows": 1,
                "manual_review_rows": 1,
            }
            assert model_counts == {
                "influencers": 1_998,
                "accounts": 1_998,
                "source_identities": 1,
                "source_states": 1_996,
                "contacts": 2,
                "current_metrics": 1_995,
                "snapshots": 1_995,
            }
            assert measurement.wall_seconds <= 60
            assert measurement.selects <= 70
            assert measurement.total <= 100
            assert measurement.rss_high_water_after_bytes <= 900 * 1024**2

    asyncio.run(scenario())


def test_repeated_same_2000_row_batch_does_not_inflate_library_or_snapshots() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            first = await preview_gate._seed_batch(
                harness, preview_gate._matrix_files(), screening=True
            )
            await preview_gate._parse_all(harness, first)
            await preview_gate._seed_matrix_database_state(harness, first)
            first_revision = await _preview(harness, first)
            await _run_confirm(harness, first, first_revision)

            repeated = await _repeat_same_batch(harness, first)
            await preview_gate._parse_all(harness, repeated)
            repeated_revision = await _preview(harness, repeated)
            async with harness.factory() as session:
                repeated_job = await session.get(ImportJob, repeated.job_id)
                assert repeated_job is not None and repeated_job.preview_summary is not None
                print(
                    "TASK6_REPEAT_PREVIEW "
                    + json.dumps(
                        {
                            key: repeated_job.preview_summary[key]
                            for key in (
                                "raw_rows",
                                "new_rows",
                                "updated_rows",
                                "no_change_rows",
                                "skipped_rows",
                                "error_rows",
                                "manual_review_rows",
                            )
                        },
                        sort_keys=True,
                    )
                )
                assert repeated_job.preview_summary["new_rows"] == 0
                # The two manual-contact-protection candidates remain UPDATE
                # suggestions by design; applying them is a no-op and never
                # overwrites the manually sourced contact facts.
                assert repeated_job.preview_summary["updated_rows"] == 2
                assert repeated_job.preview_summary["no_change_rows"] == 1_994
            result = await _run_confirm(harness, repeated, repeated_revision)
            assert result["created_rows"] == 0
            assert result["updated_rows"] == 2
            assert result["no_change_rows"] == 1_994

            async with harness.factory() as session:
                assert await session.scalar(select(func.count()).select_from(Influencer)) == 1_998
                assert (
                    await session.scalar(
                        select(func.count()).select_from(InfluencerPlatformAccount)
                    )
                    == 1_998
                )
                assert (
                    await session.scalar(select(func.count()).select_from(InfluencerMetricSnapshot))
                    == 1_995
                )
                rows = await preview_gate._job_rows(session, repeated.job_id)
                assert len(rows) == 2_000
                assert all(row.committed_at is not None for row in rows)

    asyncio.run(scenario())


def test_failure_at_row_1999_rolls_back_then_lease_recovery_completes_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            seeded = await preview_gate._seed_batch(
                harness, preview_gate._matrix_files(), screening=True
            )
            await preview_gate._parse_all(harness, seeded)
            await preview_gate._seed_matrix_database_state(harness, seeded)
            revision = await _preview(harness, seeded)
            await _request_confirm(harness, seeded, revision)
            token, generation = await _claim_confirm(harness, seeded.job_id, revision)

            async with harness.factory() as session:
                before = {
                    model: int(await session.scalar(select(func.count()).select_from(model)) or 0)
                    for model in (
                        Influencer,
                        InfluencerPlatformAccount,
                        PlatformAccountSourceIdentity,
                        InfluencerSourceState,
                        InfluencerContact,
                        InfluencerCurrentMetrics,
                        InfluencerMetricSnapshot,
                    )
                }

            original_apply = ImportMergeApplier.apply
            applied = 0

            async def fail_at_1999(
                applier: ImportMergeApplier,
                *args: object,
                **kwargs: object,
            ) -> None:
                nonlocal applied
                applied += 1
                if applied == 1_999:
                    raise RuntimeError("injected row 1999 transaction failure")
                await original_apply(applier, *args, **kwargs)  # type: ignore[arg-type]

            monkeypatch.setattr(ImportMergeApplier, "apply", fail_at_1999)
            with pytest.raises(ImportDomainError) as raised:
                await _run_claimed_confirm(
                    harness,
                    seeded,
                    revision,
                    token,
                    generation,
                )
            assert raised.value.code == "IMPORT_CONFIRM_FAILED"
            assert applied == 1_999

            async with harness.factory() as session:
                after = {
                    model: int(await session.scalar(select(func.count()).select_from(model)) or 0)
                    for model in before
                }
                assert after == before
                rows = await preview_gate._job_rows(session, seeded.job_id)
                assert all(row.committed_at is None for row in rows)
                job = await session.get(ImportJob, seeded.job_id)
                task = await session.scalar(
                    select(ImportTaskRequest).where(ImportTaskRequest.task_token == token)
                )
                assert job is not None and job.status is ImportJobStatus.CONFIRM_QUEUED
                assert task is not None and task.state is ImportTaskState.RUNNING
                assert task.completed_at is None
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(
                            AuditLog.entity_id == seeded.job_id,
                            AuditLog.action == AuditAction.IMPORT_BATCH_COMPLETED,
                        )
                    )
                    == 0
                )

            # The worker disappeared with the task still RUNNING. PostgreSQL
            # lease expiry and reconciliation must make the same token runnable
            # again, and generation 2 must perform one complete atomic merge.
            async with harness.factory() as session:
                task = await session.scalar(
                    select(ImportTaskRequest)
                    .where(ImportTaskRequest.task_token == token)
                    .with_for_update()
                )
                assert task is not None
                task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                await session.commit()
            async with harness.factory() as session:
                expired = await ImportTaskService(session, _settings()).reconcile()
                assert [item.task_token for item in expired.deferred] == [token]
                assert expired.dispatches == ()
                await session.commit()
            async with harness.factory() as session:
                task = await session.scalar(
                    select(ImportTaskRequest)
                    .where(ImportTaskRequest.task_token == token)
                    .with_for_update()
                )
                assert task is not None and task.state is ImportTaskState.RETRY_WAIT
                task.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
                await session.commit()
            async with harness.factory() as session:
                recovery = await ImportTaskService(session, _settings()).reconcile()
                assert [item.task_token for item in recovery.dispatches] == [token]
                recovery_envelope = recovery.dispatches[0]
                await session.commit()
            async with harness.factory() as session:
                second_claim = await ImportTaskService(session, _settings()).claim(
                    recovery_envelope
                )
                assert second_claim.status is ClaimStatus.CLAIMED
                assert second_claim.generation == 2
                await session.commit()

            monkeypatch.setattr(ImportMergeApplier, "apply", original_apply)
            recovered = await _run_claimed_confirm(
                harness,
                seeded,
                revision,
                token,
                2,
            )
            assert recovered == {
                "import_job_id": str(seeded.job_id),
                "preview_revision": revision,
                "created_rows": 1_994,
                "updated_rows": 1,
                "no_change_rows": 1,
                "skipped_rows": 2,
                "error_rows": 1,
                "manual_review_rows": 1,
            }
            async with harness.factory() as session:
                task = await session.scalar(
                    select(ImportTaskRequest).where(ImportTaskRequest.task_token == token)
                )
                assert task is not None and task.state is ImportTaskState.COMPLETED
                assert task.run_attempts == 2
                assert task.completed_at is not None
                rows = await preview_gate._job_rows(session, seeded.job_id)
                assert len(rows) == 2_000
                assert all(row.committed_at is not None for row in rows)
                assert all(row.committed_action is row.action for row in rows)
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(
                            AuditLog.entity_id == seeded.job_id,
                            AuditLog.action == AuditAction.IMPORT_BATCH_COMPLETED,
                            AuditLog.result == AuditResult.SUCCESS,
                        )
                    )
                    == 1
                )

    asyncio.run(scenario())


def test_concurrent_new_identity_commits_once_and_marks_other_preview_stale() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            batches = [
                await preview_gate._seed_batch(
                    harness,
                    [[preview_gate._row("shared-new-identity", nonce=f"batch-{label}")]],
                )
                for label in ("a", "b")
            ]
            for seeded in batches:
                await preview_gate._parse_all(harness, seeded)
            revisions = [await _preview(harness, seeded) for seeded in batches]
            claims: list[tuple[UUID, int]] = []
            for seeded, revision in zip(batches, revisions, strict=True):
                await _request_confirm(harness, seeded, revision)
                claims.append(await _claim_confirm(harness, seeded.job_id, revision))

            barrier = asyncio.Barrier(3)
            executions = [
                asyncio.create_task(
                    _run_claimed_confirm(
                        harness,
                        seeded,
                        revision,
                        token,
                        generation,
                        barrier,
                    )
                )
                for seeded, revision, (token, generation) in zip(
                    batches, revisions, claims, strict=True
                )
            ]
            await barrier.wait()
            results = await asyncio.wait_for(asyncio.gather(*executions), timeout=30)
            assert sum("created_rows" in result for result in results) == 1
            assert sum(result.get("status") == "preview_stale" for result in results) == 1

            async with harness.factory() as session:
                assert await session.scalar(select(func.count()).select_from(Influencer)) == 1
                assert (
                    await session.scalar(
                        select(func.count()).select_from(InfluencerPlatformAccount)
                    )
                    == 1
                )
                jobs = [await session.get(ImportJob, seeded.job_id) for seeded in batches]
                assert [job.status for job in jobs if job is not None].count(
                    ImportJobStatus.COMPLETED
                ) == 1
                assert [job.status for job in jobs if job is not None].count(
                    ImportJobStatus.PREVIEW_STALE
                ) == 1

    asyncio.run(scenario())


def test_existing_account_concurrent_update_cannot_overwrite_from_stale_preview() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            baseline = await preview_gate._seed_batch(
                harness,
                [
                    [
                        preview_gate._row(
                            "shared-existing-identity",
                            name="Baseline",
                            updated_at="2026-08-09 12:00:00",
                            followers="100",
                            nonce="baseline",
                        )
                    ]
                ],
            )
            await preview_gate._parse_all(harness, baseline)
            baseline_revision = await _preview(harness, baseline)
            await _run_confirm(harness, baseline, baseline_revision)

            batches = [
                await preview_gate._seed_batch(
                    harness,
                    [
                        [
                            preview_gate._row(
                                "shared-existing-identity",
                                name="Concurrent update",
                                updated_at="2026-08-10 12:00:00",
                                followers="300",
                                nonce=f"update-{label}",
                            )
                        ]
                    ],
                )
                for label in ("a", "b")
            ]
            for seeded in batches:
                await preview_gate._parse_all(harness, seeded)
            revisions = [await _preview(harness, seeded) for seeded in batches]
            async with harness.factory() as session:
                for seeded in batches:
                    job = await session.get(ImportJob, seeded.job_id)
                    assert job is not None and job.preview_summary is not None
                    assert job.preview_summary["updated_rows"] == 1

            claims: list[tuple[UUID, int]] = []
            for seeded, revision in zip(batches, revisions, strict=True):
                await _request_confirm(harness, seeded, revision)
                claims.append(await _claim_confirm(harness, seeded.job_id, revision))
            barrier = asyncio.Barrier(3)
            executions = [
                asyncio.create_task(
                    _run_claimed_confirm(
                        harness,
                        seeded,
                        revision,
                        token,
                        generation,
                        barrier,
                    )
                )
                for seeded, revision, (token, generation) in zip(
                    batches, revisions, claims, strict=True
                )
            ]
            await barrier.wait()
            results = await asyncio.wait_for(asyncio.gather(*executions), timeout=30)
            assert sum(result.get("updated_rows") == 1 for result in results) == 1
            assert sum(result.get("status") == "preview_stale" for result in results) == 1

            async with harness.factory() as session:
                assert await session.scalar(select(func.count()).select_from(Influencer)) == 1
                assert (
                    await session.scalar(
                        select(func.count()).select_from(InfluencerPlatformAccount)
                    )
                    == 1
                )
                current = await session.scalar(select(InfluencerCurrentMetrics))
                assert current is not None and current.metrics["followers_count"] == 300
                assert (
                    await session.scalar(select(func.count()).select_from(InfluencerMetricSnapshot))
                    == 2
                )

    asyncio.run(scenario())


def test_cross_basis_platform_id_and_profile_url_concurrent_confirm_has_one_winner() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            account_id = "cross-basis-existing"
            profile_url = (
                "https://www.xiaohongshu.com/user/profile/" f"{account_id}?source=task6-gate"
            )
            normalized_profile_url = "https://www.xiaohongshu.com/user/profile/" f"{account_id}"
            baseline = await preview_gate._seed_batch(
                harness,
                [
                    [
                        preview_gate._row(
                            account_id,
                            name="Cross-basis baseline",
                            updated_at="2026-08-09 12:00:00",
                            followers="100",
                            nonce="cross-basis-baseline",
                        )
                    ]
                ],
            )
            await preview_gate._parse_all(harness, baseline)
            baseline_revision = await _preview(harness, baseline)
            await _run_confirm(harness, baseline, baseline_revision)
            async with harness.factory() as session:
                account = await session.scalar(
                    select(InfluencerPlatformAccount).where(
                        InfluencerPlatformAccount.platform_account_id == account_id
                    )
                )
                assert account is not None
                account.profile_url = profile_url
                account.normalized_profile_url = normalized_profile_url
                await session.commit()

            platform_batch = await preview_gate._seed_batch(
                harness,
                [
                    [
                        preview_gate._row(
                            account_id,
                            name="Platform ID update",
                            updated_at="2026-08-11 12:00:00",
                            followers="311",
                            nonce="platform-id-basis",
                        )
                    ]
                ],
            )
            profile_batch = await preview_gate._seed_batch(
                harness,
                [
                    [
                        preview_gate._row(
                            "",
                            external_id=profile_url,
                            name="Profile URL update",
                            updated_at="2026-08-11 12:00:00",
                            followers="422",
                            nonce="normalized-profile-basis",
                        )
                    ]
                ],
            )
            async with harness.factory() as session:
                occurrence = await session.get(ImportJobFile, profile_batch.files[0].id)
                assert occurrence is not None
                occurrence.field_mapping = {
                    **preview_gate.MAPPING,
                    "external_id": "profile_url",
                }
                await session.commit()

            batches = (platform_batch, profile_batch)
            for seeded in batches:
                await preview_gate._parse_all(harness, seeded)
            revisions = [await _preview(harness, seeded) for seeded in batches]
            async with harness.factory() as session:
                platform_row = (await preview_gate._job_rows(session, platform_batch.job_id))[0]
                profile_row = (await preview_gate._job_rows(session, profile_batch.job_id))[0]
                platform_identity = platform_row.normalized_data["platform_identity"]
                profile_identity = profile_row.normalized_data["platform_identity"]
                assert platform_identity["platform_account_id"] == account_id
                assert platform_identity["normalized_profile_url"] is None
                assert profile_identity["normalized_profile_url"] == normalized_profile_url
                assert profile_row.raw_data["external_id"] == profile_url
                assert platform_row.action is ImportRowAction.UPDATE
                assert profile_row.action is ImportRowAction.UPDATE
                assert (
                    platform_row.matched_platform_account_id
                    == profile_row.matched_platform_account_id
                )

            claims: list[tuple[UUID, int]] = []
            for seeded, revision in zip(batches, revisions, strict=True):
                await _request_confirm(harness, seeded, revision)
                claims.append(await _claim_confirm(harness, seeded.job_id, revision))
            barrier = asyncio.Barrier(3)
            executions = [
                asyncio.create_task(
                    _run_claimed_confirm(
                        harness,
                        seeded,
                        revision,
                        token,
                        generation,
                        barrier,
                    )
                )
                for seeded, revision, (token, generation) in zip(
                    batches, revisions, claims, strict=True
                )
            ]
            await barrier.wait()
            results = await asyncio.wait_for(asyncio.gather(*executions), timeout=30)
            assert sum(result.get("updated_rows") == 1 for result in results) == 1
            assert sum(result.get("status") == "preview_stale" for result in results) == 1
            winner = next(
                index for index, result in enumerate(results) if result.get("updated_rows") == 1
            )
            expected_names = ("Platform ID update", "Profile URL update")
            expected_followers = (311, 422)

            async with harness.factory() as session:
                account = await session.scalar(select(InfluencerPlatformAccount))
                current = await session.scalar(select(InfluencerCurrentMetrics))
                assert account is not None and current is not None
                assert account.account_name == expected_names[winner]
                assert current.metrics["followers_count"] == expected_followers[winner]
                assert account.normalized_profile_url == normalized_profile_url
                assert await session.scalar(select(func.count()).select_from(Influencer)) == 1
                assert (
                    await session.scalar(
                        select(func.count()).select_from(InfluencerPlatformAccount)
                    )
                    == 1
                )
                assert (
                    await session.scalar(select(func.count()).select_from(InfluencerMetricSnapshot))
                    == 2
                )
                jobs = [await session.get(ImportJob, seeded.job_id) for seeded in batches]
                assert [job.status for job in jobs if job is not None].count(
                    ImportJobStatus.COMPLETED
                ) == 1
                assert [job.status for job in jobs if job is not None].count(
                    ImportJobStatus.PREVIEW_STALE
                ) == 1
                tasks = list(
                    await session.scalars(
                        select(ImportTaskRequest).where(
                            ImportTaskRequest.task_token.in_(token for token, _ in claims)
                        )
                    )
                )
                assert [task.state for task in tasks].count(ImportTaskState.COMPLETED) == 1
                assert [task.state for task in tasks].count(ImportTaskState.TERMINAL_FAILED) == 1

    asyncio.run(scenario())


def test_mixed_new_changed_no_change_older_same_time_contact_and_snapshot_semantics() -> None:
    async def scenario() -> None:
        async with preview_gate._isolated_postgres() as harness:
            baseline_rows = [
                preview_gate._row(
                    "mixed-changed", name="Changed baseline", followers="100", nonce="base-1"
                ),
                preview_gate._row("mixed-stable", name="Stable", followers="200", nonce="base-2"),
                preview_gate._row(
                    "mixed-older", name="Older baseline", followers="300", nonce="base-3"
                ),
                preview_gate._row(
                    "mixed-same-time",
                    name="Same-time baseline",
                    followers="400",
                    nonce="base-4",
                ),
            ]
            baseline = await preview_gate._seed_batch(harness, [baseline_rows])
            await preview_gate._parse_all(harness, baseline)
            baseline_revision = await _preview(harness, baseline)
            await _run_confirm(harness, baseline, baseline_revision)

            protected_seen_at = datetime(2026, 8, 11, 3, 0, tzinfo=UTC)
            async with harness.factory() as session:
                changed_account = await session.scalar(
                    select(InfluencerPlatformAccount).where(
                        InfluencerPlatformAccount.platform_account_id == "mixed-changed"
                    )
                )
                assert changed_account is not None
                session.add(
                    InfluencerContact(
                        influencer_id=changed_account.influencer_id,
                        platform_account_id=changed_account.id,
                        type=ContactType.EMAIL,
                        value="protected@example.invalid",
                        normalized_value="protected@example.invalid",
                        source=DataSource.MANUAL,
                        validation_status=ContactValidationStatus.VALID,
                        is_current=True,
                        possible_duplicate_contact=False,
                        first_seen_at=protected_seen_at,
                        last_seen_at=protected_seen_at,
                        source_updated_at=None,
                    )
                )
                baseline_snapshots = {
                    item.id: (
                        item.source_updated_at,
                        dict(item.metrics),
                        item.metrics_hash,
                        item.snapshot_key,
                    )
                    for item in await session.scalars(select(InfluencerMetricSnapshot))
                }
                await session.commit()

            mixed_rows = [
                preview_gate._row("mixed-new", nonce="mixed-new"),
                preview_gate._row(
                    "mixed-changed",
                    name="Changed newer",
                    updated_at="2026-08-11 12:00:00",
                    followers="150",
                    email="source@example.invalid",
                    nonce="mixed-changed",
                ),
                preview_gate._row(
                    "mixed-stable", name="Stable", followers="200", nonce="mixed-stable"
                ),
                preview_gate._row(
                    "mixed-older",
                    name="Must not overwrite from older",
                    updated_at="2026-08-09 12:00:00",
                    followers="250",
                    nonce="mixed-older",
                ),
                preview_gate._row(
                    "mixed-same-time",
                    name="Must not overwrite at same time",
                    followers="450",
                    nonce="mixed-same-time",
                ),
                preview_gate._row(
                    "mixed-email-a",
                    email="shared-mixed@example.invalid",
                    nonce="mixed-email-a",
                ),
                preview_gate._row(
                    "mixed-email-b",
                    email="shared-mixed@example.invalid",
                    nonce="mixed-email-b",
                ),
            ]
            mixed = await preview_gate._seed_batch(harness, [mixed_rows])
            await preview_gate._parse_all(harness, mixed)
            revision = await _preview(harness, mixed)

            async with harness.factory() as session:
                job = await session.get(ImportJob, mixed.job_id)
                assert job is not None and job.preview_summary is not None
                assert {
                    key: job.preview_summary[key]
                    for key in (
                        "new_rows",
                        "changed_rows",
                        "no_change_rows",
                        "possible_duplicate_contact_rows",
                    )
                } == {
                    "new_rows": 3,
                    "changed_rows": 1,
                    "no_change_rows": 3,
                    "possible_duplicate_contact_rows": 2,
                }
                rows = await preview_gate._job_rows(session, mixed.job_id)
                by_account = {
                    row.normalized_data["platform_identity"]["platform_account_id"]: row
                    for row in rows
                    if row.normalized_data is not None
                }
                assert by_account["mixed-older"].action.value == "no_change"
                assert by_account["mixed-same-time"].action.value == "no_change"
                assert "STALE_SOURCE_VALUE_IGNORED" in {
                    warning["code"] for warning in by_account["mixed-older"].warnings
                }
                assert "SAME_TIMESTAMP_CONFLICT" in {
                    warning["code"] for warning in by_account["mixed-same-time"].warnings
                }
                for account_id in ("mixed-email-a", "mixed-email-b"):
                    contact_plan = by_account[account_id].merge_plan["contacts"]["create"]
                    assert any(item["possible_duplicate_contact"] is True for item in contact_plan)

            result = await _run_confirm(harness, mixed, revision)
            assert {
                key: result[key] for key in ("created_rows", "updated_rows", "no_change_rows")
            } == {"created_rows": 3, "updated_rows": 1, "no_change_rows": 3}

            async with harness.factory() as session:
                accounts = {
                    account.platform_account_id: account
                    for account in await session.scalars(select(InfluencerPlatformAccount))
                }
                assert len(accounts) == 7
                assert accounts["mixed-changed"].account_name == "Changed newer"
                assert accounts["mixed-older"].account_name == "Older baseline"
                assert accounts["mixed-same-time"].account_name == "Same-time baseline"
                metrics = {
                    item.platform_account_id: item
                    for item in await session.scalars(select(InfluencerCurrentMetrics))
                }
                assert metrics[accounts["mixed-changed"].id].metrics["followers_count"] == 150
                assert metrics[accounts["mixed-older"].id].metrics["followers_count"] == 300
                assert metrics[accounts["mixed-same-time"].id].metrics["followers_count"] == 400

                protected = await session.scalar(
                    select(InfluencerContact).where(
                        InfluencerContact.source == DataSource.MANUAL,
                        InfluencerContact.normalized_value == "protected@example.invalid",
                    )
                )
                assert protected is not None
                assert protected.is_current is True
                assert protected.last_seen_at == protected_seen_at
                duplicate_contacts = list(
                    await session.scalars(
                        select(InfluencerContact).where(
                            InfluencerContact.normalized_value == "shared-mixed@example.invalid"
                        )
                    )
                )
                assert len(duplicate_contacts) == 2
                assert len({item.influencer_id for item in duplicate_contacts}) == 2
                assert all(item.possible_duplicate_contact for item in duplicate_contacts)

                snapshots = {
                    item.id: (
                        item.source_updated_at,
                        dict(item.metrics),
                        item.metrics_hash,
                        item.snapshot_key,
                    )
                    for item in await session.scalars(select(InfluencerMetricSnapshot))
                }
                assert {
                    item_id: snapshots[item_id] for item_id in baseline_snapshots
                } == baseline_snapshots

    asyncio.run(scenario())
