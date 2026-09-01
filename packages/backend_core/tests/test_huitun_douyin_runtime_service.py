"""SQLite vertical-slice tests for the local Huitun Douyin capture ledger."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend_core.audit.models import AuditLog
from backend_core.config.settings import Settings
from backend_core.content_activity.enums import (
    ContentActivityObservationStatus,
    ContentActivityRefreshRequestState,
    ProviderAccountIdentityNamespace,
)
from backend_core.content_activity.models import (
    ContentActivityObservation,
    ContentActivityProjection,
    ContentActivityRefreshRequest,
    ProviderAccountIdentity,
)
from backend_core.content_activity.schemas import DouyinRuntimeCaptureIngestInput
from backend_core.content_activity.service import ContentActivityService
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.growth.repository import CandidatePoolRepository
from backend_core.influencers.enums import DataSource, Platform
from backend_core.influencers.models import Influencer, InfluencerPlatformAccount
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _create_schema(connection: AsyncConnection) -> None:
    """SQLite cannot compile the project's PostgreSQL guarded metric indexes."""

    metric_table = Base.metadata.tables["influencer_current_metrics"]
    metric_indexes = tuple(metric_table.indexes)
    metric_table.indexes.clear()
    try:
        await connection.run_sync(Base.metadata.create_all)
    finally:
        metric_table.indexes.update(metric_indexes)


async def _douyin_account(session: AsyncSession) -> InfluencerPlatformAccount:
    influencer = Influencer(display_name="Douyin runtime capture fixture")
    session.add(influencer)
    await session.flush()
    account = InfluencerPlatformAccount(
        influencer_id=influencer.id,
        platform=Platform.DOUYIN,
        platform_account_id=f"douyin-{uuid4().hex}",
        account_name="Douyin runtime capture fixture",
        account_handle="douyin-runtime-fixture",
        profile_url=f"https://example.invalid/{uuid4().hex}",
        normalized_profile_url=f"https://example.invalid/{uuid4().hex}",
        source=DataSource.MANUAL,
        source_tags=[],
        is_active=True,
    )
    session.add(account)
    await session.flush()
    return account


def _semantic_input(*, request_id, uid: str = "huitun-uid-42") -> DouyinRuntimeCaptureIngestInput:
    return DouyinRuntimeCaptureIngestInput.model_validate(
        {
            "capture_request_id": request_id,
            "runtime_request_id": "runtime-request-1",
            "outcome": "SUCCESS",
            "actual_uid": uid,
            "semantic_uid": uid,
            "publications": [
                {"published_at": NOW - timedelta(days=60)},
                {"published_at": NOW - timedelta(days=10)},
            ],
            "pagination_terminal": True,
            "coverage_end_at": NOW,
        }
    )


def test_runtime_capture_works_with_provider_off_and_persists_only_normalized_ledger_data() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                account = await _douyin_account(session)
                service = ContentActivityService(
                    session,
                    Settings(_env_file=None),
                    clock=lambda: NOW,
                )
                try:
                    launch = await service.create_douyin_runtime_capture(
                        platform_account_id=account.id,
                        idempotency_key="douyin-runtime-service-launch",
                        operator_id=None,
                        department_id=None,
                        ip="127.0.0.1",
                        user_agent="runtime-service-test",
                    )
                    request = await session.scalar(
                        select(ContentActivityRefreshRequest).where(
                            ContentActivityRefreshRequest.request_token
                            == launch.capture_request_id
                        )
                    )
                    assert request is not None
                    assert request.state is ContentActivityRefreshRequestState.RUNNING
                    assert request.capture_token_digest is not None
                    assert launch.capture_token not in request.capture_token_digest
                    # Runtime bridge requests are never dispatched through the
                    # provider reconciler, even with the provider default OFF.
                    assert await service.repository.list_due_refresh_requests(
                        as_of=NOW,
                        limit=10,
                    ) == []

                    accepted = await service.ingest_douyin_runtime_capture(
                        capture_token=launch.capture_token,
                        payload=_semantic_input(request_id=launch.capture_request_id),
                    )
                    assert accepted.outcome == "ACCEPTED"
                    assert request.capture_token_digest is None
                    observation = await session.get(
                        ContentActivityObservation,
                        accepted.observation_id,
                    )
                    assert observation is not None
                    assert _utc(observation.last_publication_at) == NOW - timedelta(days=10)
                    assert observation.provider_error_code is None
                    assert observation.request_ref is not None
                    assert launch.capture_token not in observation.request_ref
                    projection = await session.scalar(
                        select(ContentActivityProjection).where(
                            ContentActivityProjection.platform_account_id == account.id
                        )
                    )
                    assert projection is not None
                    assert projection.trusted_observation_id is None
                    hydrated = await CandidatePoolRepository(session)._content_activity_facts_as_of(
                        account_ids=(account.id,),
                        as_of=NOW,
                    )
                    assert hydrated[account.id].trusted_observation_id is None
                    assert hydrated[account.id].huitun_observation_id == observation.id
                    assert hydrated[account.id].huitun_last_publication_at == NOW - timedelta(
                        days=10
                    )
                    identity = await session.scalar(
                        select(ProviderAccountIdentity).where(
                            ProviderAccountIdentity.platform_account_id == account.id,
                            ProviderAccountIdentity.namespace
                            == ProviderAccountIdentityNamespace.DOUYIN_HUITUN_UID,
                        )
                    )
                    assert identity is not None
                    assert identity.opaque_external_identity == "huitun-uid-42"

                    serialized_audit = json.dumps(
                        [record.after for record in await session.scalars(select(AuditLog))],
                        default=str,
                    )
                    assert launch.capture_token not in serialized_audit
                finally:
                    await service.aclose()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_existing_account_uid_mismatch_is_settled_as_unknown_without_rebinding() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                account = await _douyin_account(session)
                service = ContentActivityService(
                    session,
                    Settings(_env_file=None),
                    clock=lambda: NOW,
                )
                try:
                    first = await service.create_douyin_runtime_capture(
                        platform_account_id=account.id,
                        idempotency_key="douyin-runtime-first",
                        operator_id=None,
                        department_id=None,
                        ip="127.0.0.1",
                        user_agent="runtime-service-test",
                    )
                    assert (
                        await service.ingest_douyin_runtime_capture(
                            capture_token=first.capture_token,
                            payload=_semantic_input(request_id=first.capture_request_id),
                        )
                    ).outcome == "ACCEPTED"

                    second = await service.create_douyin_runtime_capture(
                        platform_account_id=account.id,
                        idempotency_key="douyin-runtime-second",
                        operator_id=None,
                        department_id=None,
                        ip="127.0.0.1",
                        user_agent="runtime-service-test",
                    )
                    rejected = await service.ingest_douyin_runtime_capture(
                        capture_token=second.capture_token,
                        payload=_semantic_input(
                            request_id=second.capture_request_id,
                            uid="different-real-aweme-uid",
                        ),
                    )
                    assert rejected.outcome == "UNKNOWN"
                    observation = await session.get(
                        ContentActivityObservation,
                        rejected.observation_id,
                    )
                    assert observation is not None
                    assert (
                        observation.observation_status
                        is ContentActivityObservationStatus.RESULT_UNTRUSTED
                    )
                    assert observation.provider_error_code == "IDENTITY_CONFLICT"
                    identity = await session.scalar(
                        select(ProviderAccountIdentity).where(
                            ProviderAccountIdentity.platform_account_id == account.id,
                            ProviderAccountIdentity.namespace
                            == ProviderAccountIdentityNamespace.DOUYIN_HUITUN_UID,
                        )
                    )
                    assert identity is not None
                    assert identity.opaque_external_identity == "huitun-uid-42"
                finally:
                    await service.aclose()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_invalid_payload_reason_is_persisted_without_semantic_data() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await _create_schema(connection)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                account = await _douyin_account(session)
                service = ContentActivityService(
                    session,
                    Settings(_env_file=None),
                    clock=lambda: NOW,
                )
                try:
                    launch = await service.create_douyin_runtime_capture(
                        platform_account_id=account.id,
                        idempotency_key="douyin-runtime-invalid-payload",
                        operator_id=None,
                        department_id=None,
                        ip="127.0.0.1",
                        user_agent="runtime-service-test",
                    )
                    rejected = await service.ingest_douyin_runtime_capture(
                        capture_token=launch.capture_token,
                        payload=DouyinRuntimeCaptureIngestInput.model_validate(
                            {
                                "capture_request_id": launch.capture_request_id,
                                "runtime_request_id": "runtime-rejected-1",
                                "outcome": "INVALID_PAYLOAD",
                                "actual_uid": "huitun-uid-42",
                                "semantic_uid": None,
                                "publications": [],
                                "pagination_terminal": None,
                                "coverage_start_at": None,
                                "coverage_end_at": None,
                                "rejection_reason": "REQUEST_BINDING_MISMATCH",
                            }
                        ),
                    )
                    assert rejected.outcome == "UNKNOWN"
                    observation = await session.get(
                        ContentActivityObservation,
                        rejected.observation_id,
                    )
                    assert observation is not None
                    assert observation.provider_error_code == "REQUEST_BINDING_MISMATCH"
                    assert observation.last_publication_at is None
                    assert observation.latest_publication_id is None
                    request = await session.scalar(
                        select(ContentActivityRefreshRequest).where(
                            ContentActivityRefreshRequest.request_token
                            == launch.capture_request_id
                        )
                    )
                    assert request is not None
                    assert request.last_error_code == "REQUEST_BINDING_MISMATCH"
                finally:
                    await service.aclose()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
