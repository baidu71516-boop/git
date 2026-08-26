"""Celery contracts for durable, UUID-only Content Activity refresh delivery."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import UUID, uuid4

import pytest
from app.celery_app import celery_app
from app.tasks import content_activity as content_activity_tasks
from backend_core.config import Settings, get_settings


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        content_activity_refresh_reconcile_interval_seconds=23,
    )


def _async_context(value: object) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=value)
    context.__aexit__ = AsyncMock(return_value=None)
    return context


def test_content_activity_tasks_are_registered_with_durable_queue_contracts() -> None:
    refresher = celery_app.tasks["content_activity.refresh_request"]
    assert refresher.ignore_result is True
    assert refresher.acks_late is True
    assert refresher.reject_on_worker_lost is True
    assert celery_app.conf.task_routes["content_activity.refresh_request"] == {"queue": "analytics"}

    reconciler = celery_app.tasks["content_activity.reconcile_refresh_requests"]
    assert reconciler.ignore_result is True
    assert celery_app.conf.task_routes["content_activity.reconcile_refresh_requests"] == {
        "queue": "default"
    }
    assert celery_app.conf.beat_schedule["reconcile-content-activity-refresh-requests"] == {
        "task": "content_activity.reconcile_refresh_requests",
        "schedule": get_settings().content_activity_refresh_reconcile_interval_seconds,
        "options": {"queue": "default"},
    }
    assert "analytics" in {queue.name for queue in celery_app.conf.task_queues}
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_refresh_entrypoint_accepts_only_one_uuid_and_delegates_that_uuid() -> None:
    request_token = uuid4()

    with patch.object(content_activity_tasks, "_refresh", AsyncMock()) as delegate:
        content_activity_tasks.refresh_request.run(str(request_token))

    delegate.assert_awaited_once_with(request_token)


def test_refresh_entrypoint_rejects_non_uuid_before_database_or_provider_work() -> None:
    with (
        patch.object(content_activity_tasks, "_refresh", AsyncMock()) as delegate,
        pytest.raises(ValueError),
    ):
        content_activity_tasks.refresh_request.run("not-a-uuid")

    delegate.assert_not_awaited()


def test_publish_refresh_emits_an_id_only_analytics_message() -> None:
    request_token = UUID("00000000-0000-0000-0000-000000000901")

    with patch.object(celery_app, "send_task") as send:
        content_activity_tasks._publish_refresh(request_token)

    send.assert_called_once_with(
        "content_activity.refresh_request",
        args=[str(request_token)],
        task_id=str(request_token),
        queue="analytics",
        retry=False,
    )


def test_refresh_opens_a_short_lived_service_and_closes_its_client() -> None:
    request_token = uuid4()
    session = MagicMock()
    database = MagicMock()
    database.session_factory.return_value = _async_context(session)
    database.close = AsyncMock(return_value=None)
    service = MagicMock()
    service.process_refresh_request = AsyncMock(return_value=SimpleNamespace(claimed=True))
    service.aclose = AsyncMock(return_value=None)

    with (
        patch.object(content_activity_tasks, "get_settings", return_value=_settings()),
        patch.object(content_activity_tasks, "Database", return_value=database),
        patch.object(
            content_activity_tasks, "ContentActivityService", return_value=service
        ) as service_type,
    ):
        claimed = asyncio.run(content_activity_tasks._refresh(request_token))

    assert claimed is True
    service_type.assert_called_once_with(session, _settings())
    service.process_refresh_request.assert_awaited_once_with(request_token)
    service.aclose.assert_awaited_once_with()
    database.close.assert_awaited_once_with()


def test_refresh_worker_redacts_unexpected_provider_or_bootstrap_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_token = uuid4()
    secret_sentinel = "RAW_BOOTSTRAP_OR_PROVIDER_DETAIL_MUST_NOT_BE_LOGGED"
    session = MagicMock()
    session.rollback = AsyncMock(return_value=None)
    database = MagicMock()
    database.session_factory.return_value = _async_context(session)
    database.close = AsyncMock(return_value=None)
    service = MagicMock()
    service.process_refresh_request = AsyncMock(side_effect=RuntimeError(secret_sentinel))
    service.aclose = AsyncMock(return_value=None)
    caplog.set_level(logging.WARNING)

    with (
        patch.object(content_activity_tasks, "get_settings", return_value=_settings()),
        patch.object(content_activity_tasks, "Database", return_value=database),
        patch.object(content_activity_tasks, "ContentActivityService", return_value=service),
    ):
        claimed = asyncio.run(content_activity_tasks._refresh(request_token))

    assert claimed is False
    session.rollback.assert_awaited_once_with()
    assert secret_sentinel not in caplog.text
    service.aclose.assert_awaited_once_with()
    database.close.assert_awaited_once_with()


def test_reconciler_republishes_only_due_tokens_and_leaves_delivery_state_to_service() -> None:
    first = uuid4()
    second = uuid4()
    session = MagicMock()
    database = MagicMock()
    database.session_factory.return_value = _async_context(session)
    database.close = AsyncMock(return_value=None)
    service = MagicMock()
    service.reconcile_due_refresh_requests = AsyncMock(return_value=(first, second))
    service.aclose = AsyncMock(return_value=None)
    publisher = MagicMock()

    with (
        patch.object(content_activity_tasks, "get_settings", return_value=_settings()),
        patch.object(content_activity_tasks, "Database", return_value=database),
        patch.object(content_activity_tasks, "ContentActivityService", return_value=service),
    ):
        result = asyncio.run(content_activity_tasks._reconcile(publisher))

    assert result == content_activity_tasks.ContentActivityReconcileResult(
        selected=2,
        published=2,
        publish_failed=0,
    )
    publisher.assert_has_calls([call(first), call(second)])
    assert publisher.call_count == 2
    service.reconcile_due_refresh_requests.assert_awaited_once_with()
    service.aclose.assert_awaited_once_with()
    database.close.assert_awaited_once_with()


def test_reconciler_keeps_due_records_recoverable_when_broker_publish_fails() -> None:
    request_token = uuid4()
    session = MagicMock()
    database = MagicMock()
    database.session_factory.return_value = _async_context(session)
    database.close = AsyncMock(return_value=None)
    service = MagicMock()
    service.reconcile_due_refresh_requests = AsyncMock(return_value=(request_token,))
    service.aclose = AsyncMock(return_value=None)
    publisher = MagicMock(side_effect=RuntimeError("broker unavailable"))

    with (
        patch.object(content_activity_tasks, "get_settings", return_value=_settings()),
        patch.object(content_activity_tasks, "Database", return_value=database),
        patch.object(content_activity_tasks, "ContentActivityService", return_value=service),
    ):
        result = asyncio.run(content_activity_tasks._reconcile(publisher))

    assert result == content_activity_tasks.ContentActivityReconcileResult(
        selected=1,
        published=0,
        publish_failed=1,
    )
    publisher.assert_called_once_with(request_token)
    service.reconcile_due_refresh_requests.assert_awaited_once_with()
    database.close.assert_awaited_once_with()
