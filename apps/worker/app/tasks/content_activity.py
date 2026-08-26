"""Durable, bounded XHS Content Activity delivery and reconciliation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from backend_core.config import get_settings
from backend_core.content_activity.service import ContentActivityService
from backend_core.db import Database

from app.celery_app import celery_app

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContentActivityReconcileResult:
    """Safe counts for one durable refresh-request republish sweep."""

    selected: int
    published: int
    publish_failed: int


def _publish_refresh(request_token: UUID) -> None:
    """Publish only the committed opaque request token to the analytics queue."""

    token = str(request_token)
    celery_app.send_task(
        "content_activity.refresh_request",
        args=[token],
        task_id=token,
        queue="analytics",
        retry=False,
    )


async def _refresh(request_token: UUID) -> bool:
    """Run one request without deriving retry behavior from Celery metadata."""

    settings = get_settings()
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session:
            service = ContentActivityService(session, settings)
            try:
                result = await service.process_refresh_request(request_token)
                return result.claimed
            except Exception:
                # Do not serialize/print provider exception text. The request was
                # already durably claimed; its finite lease/reconciler path is the
                # only authority that may make it eligible again.
                await session.rollback()
                LOGGER.warning("content_activity_refresh_worker_failure")
                return False
            finally:
                await service.aclose()
    finally:
        await database.close()


async def _reconcile(
    publisher: Callable[[UUID], None] = _publish_refresh,
) -> ContentActivityReconcileResult:
    """Republish only due native records; a lost broker write is recoverable."""

    settings = get_settings()
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session:
            service = ContentActivityService(session, settings)
            try:
                request_tokens = await service.reconcile_due_refresh_requests()
            finally:
                await service.aclose()

        published = 0
        publish_failed = 0
        for request_token in request_tokens:
            try:
                await asyncio.to_thread(publisher, request_token)
                published += 1
            except Exception:
                # The PENDING/RETRY_WAIT row stays due for a later sweep.
                publish_failed += 1
                LOGGER.warning("content_activity_refresh_dispatch_failed")
        return ContentActivityReconcileResult(
            selected=len(request_tokens),
            published=published,
            publish_failed=publish_failed,
        )
    finally:
        await database.close()


@celery_app.task(
    name="content_activity.refresh_request",
    bind=False,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)  # type: ignore[untyped-decorator]
def refresh_request(request_token: str) -> None:
    """Run one Content Activity request from its UUID-only broker payload."""

    asyncio.run(_refresh(UUID(request_token)))


@celery_app.task(
    name="content_activity.reconcile_refresh_requests",
    bind=False,
    ignore_result=True,
)  # type: ignore[untyped-decorator]
def reconcile_refresh_requests() -> None:
    """Recover native requests that were committed before a broker interruption."""

    asyncio.run(_reconcile())


__all__ = [
    "ContentActivityReconcileResult",
    "reconcile_refresh_requests",
    "refresh_request",
]
