"""Opt-in PostgreSQL 16 performance and capacity gates for Bulk Confirm.

The release benchmark deliberately uses deterministic 37-column
Huitun-compatible CSV input.  Every warm-up/measured run gets a new isolated
schema while retaining identical input bytes and SHA-256 values.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import resource
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

import pytest
import test_bulk_confirm_postgres as confirm_gate
import test_unified_preview_postgres as preview_gate
from backend_core.imports.confirm_processor import BulkConfirmProcessor
from backend_core.imports.enums import ImportRowAction, ImportSourceType
from backend_core.imports.mappings import HUITUN_FIELD_MAPPING
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

CAPACITY_ENV = "RUN_IMPORT_CAPACITY_TESTS"
BENCHMARK_SEED = "task6-huitun-confirm-v1"
HUITUN_HEADERS = tuple(HUITUN_FIELD_MAPPING)


@dataclass(frozen=True, slots=True)
class ConfirmMeasurement:
    sql: preview_gate.SqlMeasurement
    cpu_user_seconds: float
    cpu_system_seconds: float
    advisory_lock_statement_seconds: float

    @property
    def cpu_total_seconds(self) -> float:
        return self.cpu_user_seconds + self.cpu_system_seconds


@dataclass(frozen=True, slots=True)
class GateObservation:
    measurement: ConfirmMeasurement
    dataset_shas: tuple[str, ...]
    entity_counts: dict[str, int]
    result: dict[str, object]


class _AdvisoryStatementTimer:
    """Measure advisory-lock statement latency, including any lock wait.

    PostgreSQL does not expose per-transaction historic lock wait without an
    additional statistics extension.  Timing each blocking advisory-lock SQL
    statement is a deterministic upper bound: it contains lock wait plus the
    small statement execution overhead.  The isolated capacity run is
    intentionally uncontended.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._started: dict[int, float] = {}
        self.seconds = 0.0
        self._listening = False

    def _before(
        self,
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        context: object,
        _executemany: bool,
    ) -> None:
        if "PG_ADVISORY_XACT_LOCK" in statement.upper():
            self._started[id(context)] = perf_counter()

    def _after(
        self,
        _connection: object,
        _cursor: object,
        _statement: str,
        _parameters: object,
        context: object,
        _executemany: bool,
    ) -> None:
        started_at = self._started.pop(id(context), None)
        if started_at is not None:
            self.seconds += perf_counter() - started_at

    def start(self) -> None:
        assert not self._listening
        self._started.clear()
        self.seconds = 0.0
        event.listen(self._engine.sync_engine, "before_cursor_execute", self._before)
        event.listen(self._engine.sync_engine, "after_cursor_execute", self._after)
        self._listening = True

    def stop(self) -> float:
        if self._listening:
            event.remove(self._engine.sync_engine, "before_cursor_execute", self._before)
            event.remove(self._engine.sync_engine, "after_cursor_execute", self._after)
            self._listening = False
        self._started.clear()
        return self.seconds


def _huitun_rows(file_position: int, *, count: int = 500) -> list[dict[str, str]]:
    prefix = f"{BENCHMARK_SEED}-file-{file_position}"
    rows: list[dict[str, str]] = []
    for index in range(count):
        values = {header: "--" for header in HUITUN_HEADERS}
        values.update(
            {
                "达人名称": f"Synthetic {prefix} row {index}",
                "达人官方地址": (
                    "https://www.xiaohongshu.com/user/profile/" f"{prefix}-row-{index}"
                ),
                "更新时间": "2026-08-10 12:00:00",
                "粉丝数": str(10_000 + index),
            }
        )
        rows.append(values)
    return rows


def _huitun_files(file_count: int) -> list[list[dict[str, str]]]:
    return [_huitun_rows(position) for position in range(1, file_count + 1)]


def _dataset_shas(files: list[list[dict[str, str]]]) -> tuple[str, ...]:
    return tuple(
        hashlib.sha256(preview_gate._csv_bytes(rows, headers=HUITUN_HEADERS)).hexdigest()
        for rows in files
    )


def _cpu_seconds() -> tuple[float, float]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return float(usage.ru_utime), float(usage.ru_stime)


def _nearest_rank_p95(values: list[float | int]) -> float:
    assert values
    ordered = sorted(float(value) for value in values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


async def _entity_counts(session: AsyncSession) -> dict[str, int]:
    models = {
        "influencers": Influencer,
        "accounts": InfluencerPlatformAccount,
        "source_identities": PlatformAccountSourceIdentity,
        "source_states": InfluencerSourceState,
        "contacts": InfluencerContact,
        "current_metrics": InfluencerCurrentMetrics,
        "snapshots": InfluencerMetricSnapshot,
        "import_rows": preview_gate.ImportRow,
    }
    return {
        label: int(await session.scalar(select(func.count()).select_from(model)) or 0)
        for label, model in models.items()
    }


async def _assert_lineage(
    session: AsyncSession,
    job_id: UUID,
    preview_lineage: dict[UUID, tuple[ImportRowAction, str, str]],
) -> None:
    rows = await preview_gate._job_rows(session, job_id)
    accounts = {
        account.id: account for account in await session.scalars(select(InfluencerPlatformAccount))
    }
    assert len(rows) == len(preview_lineage)
    assert len(accounts) == len(preview_lineage)
    for row in rows:
        expected_action, expected_plan_hash, expected_profile = preview_lineage[row.id]
        assert row.action is expected_action is ImportRowAction.CREATE
        assert row.committed_action is expected_action
        assert row.plan_hash == expected_plan_hash
        assert row.committed_at is not None
        assert row.matched_influencer_id is not None
        assert row.matched_platform_account_id is not None
        account = accounts[row.matched_platform_account_id]
        assert account.influencer_id == row.matched_influencer_id
        assert account.normalized_profile_url == expected_profile


async def _measure_confirm(
    harness: preview_gate.PostgresHarness,
    seeded: preview_gate.SeededBatch,
    revision: int,
) -> tuple[ConfirmMeasurement, dict[str, object]]:
    await confirm_gate._request_confirm(harness, seeded, revision)
    token, generation = await confirm_gate._claim_confirm(harness, seeded.job_id, revision)
    advisory_timer = _AdvisoryStatementTimer(harness.probe.engine)
    cpu_user_before, cpu_system_before = _cpu_seconds()
    advisory_timer.start()
    harness.probe.start()
    try:
        async with harness.factory() as session:
            result = await BulkConfirmProcessor(
                session,
                harness.storage,
                parser_limits=preview_gate.ParserLimits(
                    max_rows=10_000,
                    max_columns=100,
                    max_cells=1_000_000,
                ),
                settings=confirm_gate._settings(),
                max_batch_rows=10_000,
            ).confirm(seeded.job_id, revision, token, generation)
    finally:
        sql = harness.probe.stop(rows=0)
        advisory_seconds = advisory_timer.stop()
    cpu_user_after, cpu_system_after = _cpu_seconds()
    return (
        ConfirmMeasurement(
            sql=preview_gate.SqlMeasurement(
                rows=int(result["created_rows"]),
                total=sql.total,
                selects=sql.selects,
                dml=sql.dml,
                advisory=sql.advisory,
                wall_seconds=sql.wall_seconds,
                rss_high_water_before_bytes=sql.rss_high_water_before_bytes,
                rss_high_water_after_bytes=sql.rss_high_water_after_bytes,
            ),
            cpu_user_seconds=max(0.0, cpu_user_after - cpu_user_before),
            cpu_system_seconds=max(0.0, cpu_system_after - cpu_system_before),
            advisory_lock_statement_seconds=advisory_seconds,
        ),
        result,
    )


async def _run_gate(*, file_count: int) -> GateObservation:
    files = _huitun_files(file_count)
    expected_shas = _dataset_shas(files)
    expected_rows = file_count * 500
    async with preview_gate._isolated_postgres() as harness:
        seeded = await preview_gate._seed_batch(
            harness,
            files,
            source_type=ImportSourceType.MANUAL_HUITUN_EXPORT,
            field_mapping=dict(HUITUN_FIELD_MAPPING),
            csv_headers=HUITUN_HEADERS,
        )
        async with harness.factory() as session:
            actual_shas: list[str] = []
            for occurrence in seeded.files:
                stored = await session.get(preview_gate.StoredImportFile, occurrence.stored_file_id)
                assert stored is not None
                actual_shas.append(stored.sha256)
            assert tuple(actual_shas) == expected_shas

        await preview_gate._parse_all(harness, seeded)
        revision = await confirm_gate._preview(harness, seeded)
        async with harness.factory() as session:
            preview_rows = await preview_gate._job_rows(session, seeded.job_id)
            assert len(preview_rows) == expected_rows
            preview_lineage = {
                row.id: (
                    row.action,
                    row.plan_hash,
                    row.normalized_data["platform_identity"]["normalized_profile_url"],
                )
                for row in preview_rows
                if row.normalized_data is not None
            }
            assert len(preview_lineage) == expected_rows
            assert all(
                action is ImportRowAction.CREATE
                for action, _plan_hash, _profile in preview_lineage.values()
            )

        measurement, result = await _measure_confirm(harness, seeded, revision)
        assert result == {
            "import_job_id": str(seeded.job_id),
            "preview_revision": 1,
            "created_rows": expected_rows,
            "updated_rows": 0,
            "no_change_rows": 0,
            "skipped_rows": 0,
            "error_rows": 0,
            "manual_review_rows": 0,
        }
        async with harness.factory() as session:
            counts = await _entity_counts(session)
            assert counts == {
                "influencers": expected_rows,
                "accounts": expected_rows,
                "source_identities": 0,
                "source_states": expected_rows,
                "contacts": 0,
                "current_metrics": expected_rows,
                "snapshots": expected_rows,
                "import_rows": expected_rows,
            }
            await _assert_lineage(
                session,
                seeded.job_id,
                preview_lineage,
            )

        return GateObservation(
            measurement=measurement,
            dataset_shas=expected_shas,
            entity_counts=counts,
            result=result,
        )


def _observation_document(
    observation: GateObservation,
    *,
    phase: str,
    run_number: int,
) -> dict[str, object]:
    measurement = observation.measurement
    sql = measurement.sql
    return {
        "benchmark_seed": BENCHMARK_SEED,
        "phase": phase,
        "run": run_number,
        "rows": sql.rows,
        "dataset_shas": observation.dataset_shas,
        "wall_seconds": round(sql.wall_seconds, 6),
        "sql_total": sql.total,
        "sql_select": sql.selects,
        "sql_dml": sql.dml,
        "sql_advisory": sql.advisory,
        "rss_high_water_before_bytes": sql.rss_high_water_before_bytes,
        "rss_high_water_after_bytes": sql.rss_high_water_after_bytes,
        "rss_high_water_delta_bytes": sql.rss_high_water_delta_bytes,
        "cpu_user_seconds": round(measurement.cpu_user_seconds, 6),
        "cpu_system_seconds": round(measurement.cpu_system_seconds, 6),
        "cpu_total_seconds": round(measurement.cpu_total_seconds, 6),
        "advisory_lock_statement_seconds": round(measurement.advisory_lock_statement_seconds, 6),
        "entity_counts": observation.entity_counts,
    }


@pytest.mark.skipif(
    os.environ.get(preview_gate.PERFORMANCE_ENV) != "1",
    reason=f"set {preview_gate.PERFORMANCE_ENV}=1 to run the formal benchmark",
)
def test_formal_four_by_500_huitun_confirm_benchmark() -> None:
    """One warm-up plus five isolated measured runs, summarized by nearest-rank P95."""

    async def scenario() -> None:
        warmup = await _run_gate(file_count=4)
        print(
            "TASK6_CONFIRM_BENCHMARK_RUN "
            + json.dumps(
                _observation_document(warmup, phase="warmup", run_number=0),
                sort_keys=True,
            )
        )

        measured: list[GateObservation] = []
        for run_number in range(1, 6):
            observation = await _run_gate(file_count=4)
            assert observation.dataset_shas == warmup.dataset_shas
            measured.append(observation)
            print(
                "TASK6_CONFIRM_BENCHMARK_RUN "
                + json.dumps(
                    _observation_document(
                        observation,
                        phase="measured",
                        run_number=run_number,
                    ),
                    sort_keys=True,
                )
            )

        summary = {
            "benchmark_seed": BENCHMARK_SEED,
            "rows": 2_000,
            "warmup_runs": 1,
            "measured_runs": len(measured),
            "p95_algorithm": "nearest_rank_ceiling",
            "wall_seconds_p95": round(
                _nearest_rank_p95([item.measurement.sql.wall_seconds for item in measured]),
                6,
            ),
            "sql_total_p95": _nearest_rank_p95([item.measurement.sql.total for item in measured]),
            "rss_high_water_after_bytes_p95": _nearest_rank_p95(
                [item.measurement.sql.rss_high_water_after_bytes for item in measured]
            ),
            "rss_high_water_delta_bytes_p95": _nearest_rank_p95(
                [item.measurement.sql.rss_high_water_delta_bytes for item in measured]
            ),
            "cpu_total_seconds_p95": round(
                _nearest_rank_p95([item.measurement.cpu_total_seconds for item in measured]),
                6,
            ),
        }
        print("TASK6_CONFIRM_BENCHMARK_SUMMARY " + json.dumps(summary, sort_keys=True))

        assert len(measured) >= 5
        assert summary["wall_seconds_p95"] <= 60
        assert summary["rss_high_water_after_bytes_p95"] <= 900 * 1024**2
        assert max(item.measurement.sql.selects for item in measured) <= 70
        assert max(item.measurement.sql.total for item in measured) <= 100

    asyncio.run(scenario())


@pytest.mark.skipif(
    os.environ.get(CAPACITY_ENV) != "1",
    reason=f"set {CAPACITY_ENV}=1 to run the 5000-row capacity observation",
)
def test_five_thousand_huitun_confirm_capacity_observation() -> None:
    """Record 5000-row correctness/resources; elapsed time is observational."""

    async def scenario() -> None:
        observation = await _run_gate(file_count=10)
        document = _observation_document(
            observation,
            phase="capacity",
            run_number=1,
        )
        print("TASK6_CONFIRM_CAPACITY " + json.dumps(document, sort_keys=True))
        assert document["rows"] == 5_000
        assert document["rss_high_water_after_bytes"] > 0
        assert document["cpu_total_seconds"] > 0
        assert document["sql_total"] <= 160
        assert document["sql_select"] <= 110

    asyncio.run(scenario())
