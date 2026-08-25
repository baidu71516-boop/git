"""Celery delivery contracts for durable Candidate Pool materialization runs."""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import UUID, uuid4

import pytest
from app.celery_app import celery_app
from app.tasks import targeting as targeting_tasks
from backend_core.config import Settings
from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityResult,
)
from backend_core.growth.service import TargetingError
from backend_core.growth.targeting import ContentActivityFact

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REQUIRED_WORKER_QUEUES = {
    "default",
    "import",
    "ai",
    "email",
    "analytics",
    "targeting",
}


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        freshness_fresh_days=3,
        freshness_aging_days=10,
        freshness_stale_days=20,
    )


def _async_context(value: object) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=value)
    context.__aexit__ = AsyncMock(return_value=None)
    return context


def test_targeting_tasks_routes_and_reconciler_are_registered() -> None:
    materializer = celery_app.tasks["targeting.materialize_candidate_pool_run"]
    assert materializer.ignore_result is True
    assert materializer.acks_late is True
    assert materializer.reject_on_worker_lost is True
    assert celery_app.conf.task_routes["targeting.materialize_candidate_pool_run"] == {
        "queue": "targeting"
    }

    long_inactivity_materializer = celery_app.tasks[
        "targeting.materialize_candidate_pool_run_with_long_inactivity_enrichment"
    ]
    assert long_inactivity_materializer.ignore_result is True
    assert long_inactivity_materializer.acks_late is True
    assert long_inactivity_materializer.reject_on_worker_lost is True
    assert celery_app.conf.task_routes[
        "targeting.materialize_candidate_pool_run_with_long_inactivity_enrichment"
    ] == {"queue": "analytics"}

    reconciler = celery_app.tasks["targeting.reconcile_pending_candidate_pool_runs"]
    assert reconciler.ignore_result is True
    assert celery_app.conf.task_routes["targeting.reconcile_pending_candidate_pool_runs"] == {
        "queue": "default"
    }
    schedule = celery_app.conf.beat_schedule["reconcile-pending-candidate-pool-runs"]
    assert schedule == {
        "task": "targeting.reconcile_pending_candidate_pool_runs",
        "schedule": 60,
        "options": {"queue": "default"},
    }


def test_rendered_production_worker_consumes_targeting_and_existing_queues() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "-f",
            "docker-compose.yml",
            "-f",
            "infrastructure/production/docker-compose.loopback.yml",
            "config",
            "--no-env-resolution",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        cwd=PROJECT_ROOT,
        text=True,
    )
    rendered = json.loads(result.stdout)
    command = rendered["services"]["worker"]["command"]
    queue_arguments = [argument for argument in command if argument.startswith("--queues=")]

    assert queue_arguments == ["--queues=default,import,ai,email,analytics,targeting"]
    assert set(queue_arguments[0].removeprefix("--queues=").split(",")) == REQUIRED_WORKER_QUEUES


def test_materializer_entrypoint_validates_and_delegates_id_only_payload() -> None:
    run_id = uuid4()

    with patch.object(targeting_tasks, "_materialize", AsyncMock()) as delegate:
        targeting_tasks.materialize_candidate_pool_run.run(str(run_id))

    delegate.assert_awaited_once_with(run_id)


def test_long_inactivity_materializer_uses_the_same_id_only_runtime_path() -> None:
    run_id = uuid4()

    with patch.object(targeting_tasks, "_materialize", AsyncMock()) as delegate:
        targeting_tasks.materialize_candidate_pool_run_with_long_inactivity_enrichment.run(
            str(run_id)
        )

    delegate.assert_awaited_once_with(run_id)


def test_materializer_rejects_invalid_broker_identifier_before_database_work() -> None:
    with pytest.raises(ValueError):
        targeting_tasks.materialize_candidate_pool_run.run("not-a-uuid")


def test_publish_run_uses_only_the_durable_run_id() -> None:
    run_id = uuid4()

    with patch.object(celery_app, "send_task") as send:
        targeting_tasks._publish_run(run_id)

    send.assert_called_once_with(
        "targeting.materialize_candidate_pool_run",
        kwargs={"run_id": str(run_id)},
        task_id=str(run_id),
        queue="targeting",
        retry=False,
    )


def test_materialize_builds_service_with_configured_freshness() -> None:
    run_id = uuid4()
    settings = _settings()
    session = MagicMock()
    database = MagicMock()
    database.session_factory.return_value = _async_context(session)
    database.close = AsyncMock(return_value=None)
    service = MagicMock()
    service.materialize_run = AsyncMock(return_value=MagicMock())

    with (
        patch.object(targeting_tasks, "get_settings", return_value=settings),
        patch.object(targeting_tasks, "Database", return_value=database),
        patch.object(targeting_tasks, "CandidatePoolService", return_value=service) as service_type,
    ):
        result = asyncio.run(targeting_tasks._materialize(run_id))

    assert result is True
    freshness_policy = service_type.call_args.kwargs["freshness_policy"]
    assert freshness_policy.fresh_duration.days == 3
    assert freshness_policy.aging_duration.days == 10
    assert freshness_policy.stale_duration.days == 20
    assert service_type.call_args.kwargs["long_inactivity_provider_budget"] == 0
    assert callable(service_type.call_args.kwargs["long_inactivity_provider_enricher"])
    service.materialize_run.assert_awaited_once_with(run_id)
    database.close.assert_awaited_once_with()


def test_materialize_preserves_normalized_targeting_errors() -> None:
    run_id = uuid4()
    session = MagicMock()
    database = MagicMock()
    database.session_factory.return_value = _async_context(session)
    database.close = AsyncMock(return_value=None)
    service = MagicMock()
    service.materialize_run = AsyncMock(
        side_effect=TargetingError(409, "CANDIDATE_POOL_INACTIVE", "safe message")
    )

    with (
        patch.object(targeting_tasks, "get_settings", return_value=_settings()),
        patch.object(targeting_tasks, "Database", return_value=database),
        patch.object(targeting_tasks, "CandidatePoolService", return_value=service),
        pytest.raises(TargetingError, match="safe message"),
    ):
        asyncio.run(targeting_tasks._materialize(run_id))

    database.close.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("attempt_observation_id", "trusted_observation_id", "accepted"),
    [
        pytest.param(uuid4(), uuid4(), False, id="failed-attempt-preserves-old-trusted-fact"),
        pytest.param(uuid4(), None, True, id="attempt-accepted-current-trusted-fact"),
    ],
)
def test_long_inactivity_worker_attests_only_to_this_attempts_accepted_observation(
    attempt_observation_id: UUID,
    trusted_observation_id: UUID | None,
    accepted: bool,
) -> None:
    run_id = uuid4()
    account_id = uuid4()
    observed_at = datetime.now(UTC) - timedelta(days=8)
    trusted_observation_id = trusted_observation_id or attempt_observation_id
    stale_fact = ContentActivityFact(
        trusted_observation_id=trusted_observation_id,
        trusted_observed_at=observed_at,
        trusted_observation_status=ContentActivityObservationStatus.COMPLETE,
        trusted_coverage_status=ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
        trusted_activity_result=ContentActivityResult.PUBLICATION_FOUND,
        last_publication_at=observed_at - timedelta(days=120),
    )
    session = MagicMock()
    session.rollback = AsyncMock(return_value=None)
    database = MagicMock()
    database.session_factory.return_value = _async_context(session)
    content_activity_service = MagicMock()
    content_activity_service.create_xhs_refresh_requests = AsyncMock(
        return_value=(SimpleNamespace(request_token=uuid4()),)
    )
    content_activity_service.process_refresh_request = AsyncMock(
        return_value=SimpleNamespace(observation_id=attempt_observation_id)
    )
    content_activity_service.aclose = AsyncMock(return_value=None)
    repository = MagicMock()
    repository._content_activity_facts_as_of = AsyncMock(return_value={account_id: stale_fact})

    with (
        patch.object(
            targeting_tasks,
            "ContentActivityService",
            return_value=content_activity_service,
        ),
        patch.object(targeting_tasks, "CandidatePoolRepository", return_value=repository),
    ):
        result = asyncio.run(
            targeting_tasks._enrich_long_inactivity_account(
                database=database,
                settings=_settings(),
                run_id=run_id,
                platform_account_id=account_id,
            )
        )

    assert result.content_activity is stale_fact
    assert result.accepted_trusted_observation is accepted
    assert stale_fact.trusted_observation_id == trusted_observation_id
    assert stale_fact.trusted_observed_at == observed_at
    session.rollback.assert_awaited_once_with()
    content_activity_service.aclose.assert_awaited_once_with()


def test_pending_run_reconciler_republishes_only_pending_ids_in_a_bounded_sweep() -> None:
    first = uuid4()
    second = uuid4()
    session = MagicMock()
    session.scalars = AsyncMock(return_value=[first, second])
    session.rollback = AsyncMock(return_value=None)
    database = MagicMock()
    database.session_factory.return_value = _async_context(session)
    database.close = AsyncMock(return_value=None)
    publisher = MagicMock()

    with (
        patch.object(targeting_tasks, "get_settings", return_value=_settings()),
        patch.object(targeting_tasks, "Database", return_value=database),
    ):
        result = asyncio.run(targeting_tasks._reconcile_pending_runs(publisher))

    assert result == targeting_tasks.TargetingReconcileResult(
        selected=2,
        published=2,
        publish_failed=0,
    )
    publisher.assert_has_calls([call(first), call(second)])
    assert publisher.call_count == 2
    session.rollback.assert_awaited_once_with()
    database.close.assert_awaited_once_with()


def test_pending_run_reconciler_keeps_failed_publications_eligible_for_replay() -> None:
    run_id = uuid4()
    session = MagicMock()
    session.scalars = AsyncMock(return_value=[run_id])
    session.rollback = AsyncMock(return_value=None)
    database = MagicMock()
    database.session_factory.return_value = _async_context(session)
    database.close = AsyncMock(return_value=None)
    publisher = MagicMock(side_effect=RuntimeError("broker unavailable"))

    with (
        patch.object(targeting_tasks, "get_settings", return_value=_settings()),
        patch.object(targeting_tasks, "Database", return_value=database),
    ):
        result = asyncio.run(targeting_tasks._reconcile_pending_runs(publisher))

    assert result == targeting_tasks.TargetingReconcileResult(
        selected=1,
        published=0,
        publish_failed=1,
    )
    publisher.assert_called_once_with(run_id)


def test_reconciler_query_is_limited_to_the_configured_batch_size() -> None:
    session = MagicMock()
    session.scalars = AsyncMock(return_value=[])
    session.rollback = AsyncMock(return_value=None)
    database = MagicMock()
    database.session_factory.return_value = _async_context(session)
    database.close = AsyncMock(return_value=None)

    with (
        patch.object(targeting_tasks, "get_settings", return_value=_settings()),
        patch.object(targeting_tasks, "Database", return_value=database),
    ):
        asyncio.run(targeting_tasks._reconcile_pending_runs())

    statement = session.scalars.await_args.args[0]
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert f"LIMIT {targeting_tasks.PENDING_RUN_RECONCILE_BATCH_SIZE}" in compiled
