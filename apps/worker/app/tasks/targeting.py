"""Celery delivery for durable Candidate Pool materialization runs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from backend_core.config import Settings, get_settings
from backend_core.content_activity.service import ContentActivityError, ContentActivityService
from backend_core.db import Database
from backend_core.growth.enums import CandidatePoolRunStatus
from backend_core.growth.models import CandidatePoolRun
from backend_core.growth.repository import CandidatePoolRepository
from backend_core.growth.service import CandidatePoolService, TargetingError
from backend_core.growth.targeting import ContentActivityFact
from backend_core.influencers.freshness import (
    ContentActivityFreshnessPolicy,
    FreshnessPolicy,
    GreyDolphinActivityFreshnessPolicy,
)
from sqlalchemy import select

from app.celery_app import celery_app

LOGGER = logging.getLogger(__name__)
PENDING_RUN_RECONCILE_BATCH_SIZE = 100


@dataclass(frozen=True, slots=True)
class TargetingReconcileResult:
    """Counts from one bounded replay-safe pending-run sweep."""

    selected: int
    published: int
    publish_failed: int


def _freshness_policy(settings: Settings) -> FreshnessPolicy:
    """Build the same canonical freshness policy used by API-side services."""

    return FreshnessPolicy.from_day_thresholds(
        fresh_days=settings.freshness_fresh_days,
        aging_days=settings.freshness_aging_days,
        stale_days=settings.freshness_stale_days,
    )


def _content_activity_freshness_policy(settings: Settings) -> ContentActivityFreshnessPolicy:
    """Keep worker materialization aligned with the read-side activity policy."""

    return ContentActivityFreshnessPolicy.from_day_threshold(
        settings.content_activity_trusted_freshness_days
    )


def _grey_dolphin_activity_freshness_policy(
    settings: Settings,
) -> GreyDolphinActivityFreshnessPolicy:
    return GreyDolphinActivityFreshnessPolicy.from_day_threshold(
        settings.grey_dolphin_activity_freshness_days
    )


def _long_inactivity_provider_budget(settings: Settings) -> int:
    """Expose no enrichment allowance until every existing governance gate passes."""

    if not (
        settings.content_activity_enabled
        and settings.content_activity_xhs_enabled
        and settings.content_activity_provider_governance_approved
    ):
        return 0
    return settings.content_activity_provider_max_calls_per_run


async def _materialize(run_id: UUID) -> bool:
    """Delegate one ID-only delivery to the durable Candidate Pool service."""

    settings = get_settings()
    database = Database(settings.database_url)

    async def enrich_long_inactivity_account(
        platform_account_id: UUID,
    ) -> ContentActivityFact | None:
        """Use one native leased Content Activity request for one XHS candidate.

        The surrounding Candidate Run transaction is intentionally separate
        from the provider request transaction.  A worker loss rolls the run
        back to PENDING while this durable request/projection remains reusable
        as trusted cache on redelivery.
        """

        async with database.session_factory() as enrichment_session:
            service = ContentActivityService(enrichment_session, settings)
            try:
                refreshes = await service.create_xhs_refresh_requests(
                    platform_account_ids=(platform_account_id,),
                    idempotency_key=(f"candidate-long-inactivity:{run_id}:{platform_account_id}"),
                    operator_id=None,
                    department_id=None,
                    ip="worker",
                    user_agent="celery:long_inactivity_enrichment",
                )
                if len(refreshes) != 1:
                    await enrichment_session.rollback()
                    return None
                await service.process_refresh_request(refreshes[0].request_token)
                facts = await CandidatePoolRepository(
                    enrichment_session
                )._content_activity_facts_as_of(
                    account_ids=(platform_account_id,),
                    as_of=datetime.now(UTC),
                )
                await enrichment_session.rollback()
                return facts.get(platform_account_id)
            except (ContentActivityError, ValueError):
                await enrichment_session.rollback()
                return None
            except Exception:
                # The Content Activity service holds the secret-free failure
                # observation/lease authority. The targeting outcome is only
                # UNKNOWN; never surface provider exception text here.
                await enrichment_session.rollback()
                LOGGER.warning("long_inactivity_provider_enrichment_failed")
                return None
            finally:
                await service.aclose()

    try:
        async with database.session_factory() as session:
            service = CandidatePoolService(
                session,
                freshness_policy=_freshness_policy(settings),
                content_activity_freshness_policy=_content_activity_freshness_policy(settings),
                grey_dolphin_activity_freshness_policy=(
                    _grey_dolphin_activity_freshness_policy(settings)
                ),
                long_inactivity_provider_enricher=enrich_long_inactivity_account,
                long_inactivity_provider_budget=_long_inactivity_provider_budget(settings),
            )
            try:
                run = await service.materialize_run(run_id)
            except TargetingError as error:
                # Targeting errors have already been normalized by the domain
                # service. Keep the broker edge free of request payload details.
                LOGGER.warning("Candidate Pool materialization rejected: %s", error.code)
                raise
            # The task result is ignored. Avoid inspecting a replayed ORM row
            # after the service has rolled its transaction back.
            return run is not None
    finally:
        await database.close()


def _publish_run(run_id: UUID) -> None:
    """Publish the durable run identifier only; redelivery is status-safe."""

    celery_app.send_task(
        "targeting.materialize_candidate_pool_run",
        kwargs={"run_id": str(run_id)},
        task_id=str(run_id),
        queue="targeting",
        retry=False,
    )


async def _reconcile_pending_runs(
    publisher: Callable[[UUID], None] = _publish_run,
) -> TargetingReconcileResult:
    """Republish a bounded set of still-pending runs without a second task table.

    The materializer locks the run and claims only ``PENDING`` state, so a
    concurrent initial delivery or duplicate republish cannot create a second
    materialization.
    """

    settings = get_settings()
    database = Database(settings.database_url)
    try:
        async with database.session_factory() as session:
            statement = (
                select(CandidatePoolRun.id)
                .where(CandidatePoolRun.status == CandidatePoolRunStatus.PENDING)
                .order_by(CandidatePoolRun.created_at, CandidatePoolRun.id)
                .limit(PENDING_RUN_RECONCILE_BATCH_SIZE)
            )
            run_ids = tuple(await session.scalars(statement))
            await session.rollback()

        published = 0
        publish_failed = 0
        for run_id in run_ids:
            try:
                await asyncio.to_thread(publisher, run_id)
                published += 1
            except Exception:
                # The durable PENDING state remains eligible for the next sweep.
                publish_failed += 1
                LOGGER.warning("Candidate Pool run dispatch failed")
        return TargetingReconcileResult(
            selected=len(run_ids),
            published=published,
            publish_failed=publish_failed,
        )
    finally:
        await database.close()


@celery_app.task(
    name="targeting.materialize_candidate_pool_run",
    bind=False,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)  # type: ignore[untyped-decorator]
def materialize_candidate_pool_run(run_id: str) -> None:
    """Materialize one Candidate Pool run from its durable identifier."""

    asyncio.run(_materialize(UUID(run_id)))


@celery_app.task(
    name="targeting.materialize_candidate_pool_run_with_long_inactivity_enrichment",
    bind=False,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
)  # type: ignore[untyped-decorator]
def materialize_candidate_pool_run_with_long_inactivity_enrichment(run_id: str) -> None:
    """Materialize the opt-in D1A.1 execution on the analytics worker queue."""

    asyncio.run(_materialize(UUID(run_id)))


@celery_app.task(
    name="targeting.reconcile_pending_candidate_pool_runs",
    bind=False,
    ignore_result=True,
)  # type: ignore[untyped-decorator]
def reconcile_pending_candidate_pool_runs() -> None:
    """Recover pending run publications that were lost before broker delivery."""

    asyncio.run(_reconcile_pending_runs())


__all__ = [
    "PENDING_RUN_RECONCILE_BATCH_SIZE",
    "TargetingReconcileResult",
    "materialize_candidate_pool_run",
    "materialize_candidate_pool_run_with_long_inactivity_enrichment",
    "reconcile_pending_candidate_pool_runs",
]
