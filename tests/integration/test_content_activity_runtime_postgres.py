"""Real-PostgreSQL runtime coverage for D1A Content Activity cross-slice behavior.

This module is deliberately opt-in.  It accepts only a local, explicitly named
``phase1b_test`` PostgreSQL database, migrates a random schema to ``head``, and
drops that schema after each test.  It never creates a network transport or
uses a provider credential.
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.auth.service import AuthContext
from backend_core.config import Settings, get_settings
from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityProviderErrorClass,
    ContentActivityPublicationType,
    ContentActivityRefreshRequestState,
    ContentActivityResult,
    ContentActivityScanTerminalReason,
)
from backend_core.content_activity.models import (
    ContentActivityProjection,
    ContentActivityRefreshRequest,
    ProviderAccountIdentity,
)
from backend_core.content_activity.service import ContentActivityError, ContentActivityService
from backend_core.content_activity.tikhub_xhs import (
    HttpxAsyncTransport,
    TikHubXhsClient,
    XhsActivityAttempt,
    XhsIdentityResolution,
)
from backend_core.growth.enums import CandidateResult
from backend_core.growth.schemas import CandidatePoolCreateInput
from backend_core.growth.service import CandidatePoolService
from backend_core.growth.targeting import (
    ContentActivityConstraint,
    SellerTargetingPolicy,
    TargetingReasonCode,
)
from backend_core.influencers.enums import (
    ContentActivityFilter,
    CRMStage,
    DataSource,
    InfluencerStatus,
    Platform,
)
from backend_core.influencers.freshness import ContentActivityFreshnessPolicy, FreshnessPolicy
from backend_core.influencers.models import Influencer, InfluencerPlatformAccount
from backend_core.influencers.repository import InfluencerRepository
from backend_core.influencers.schemas import InfluencerListQuery
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "infrastructure" / "migrations" / "alembic.ini"
MIGRATIONS = PROJECT_ROOT / "infrastructure" / "migrations"
RUN_AS_OF = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _test_database_url() -> URL:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("TEST_DATABASE_URL is not set", allow_module_level=True)
    try:
        url = make_url(raw_url)
    except ArgumentError as error:
        pytest.fail(f"TEST_DATABASE_URL is invalid: {error}", pytrace=False)
    database_name = (url.database or "").lower()
    if (
        url.get_backend_name() != "postgresql"
        or "phase1b_test" not in database_name
        or (url.host or "").lower() not in {"127.0.0.1", "localhost"}
    ):
        pytest.fail(
            "TEST_DATABASE_URL must identify a local, explicitly named PostgreSQL test database",
            pytrace=False,
        )
    return url.set(drivername="postgresql+psycopg")


@pytest.fixture
def runtime_database_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[URL]:
    """Migrate one independent schema without touching any shared database state."""

    database_url = _test_database_url()
    schema_name = f"content_activity_runtime_{uuid4().hex}"
    admin_engine: Engine = create_engine(database_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(CreateSchema(schema_name))
    scoped_url = database_url.update_query_dict({"options": f"-csearch_path={schema_name}"})
    monkeypatch.setenv(
        "DATABASE_URL", scoped_url.render_as_string(hide_password=False).replace("%", "%%")
    )
    get_settings.cache_clear()
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS))
    try:
        command.upgrade(config, "head")
        yield scoped_url
    finally:
        get_settings.cache_clear()
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
        admin_engine.dispose()


@dataclass(slots=True)
class IncrementingClock:
    """A deterministic service clock whose values preserve write ordering."""

    next_value: datetime
    step: timedelta = timedelta(seconds=1)

    def __call__(self) -> datetime:
        result = self.next_value
        self.next_value += self.step
        return result


@dataclass(slots=True)
class ScriptedXhsClient:
    """Strict test double: no transport, retry, secret, or raw response storage."""

    attempts_by_userid: dict[str, deque[XhsActivityAttempt]]
    calls: list[str] = field(default_factory=list)

    async def resolve_user(self, *, bootstrap_input: str) -> XhsIdentityResolution:
        # The bootstrap text is only a local test value.  The production service
        # still enforces an exact provider resolver contract before binding it.
        return XhsIdentityResolution(
            observation_status=ContentActivityObservationStatus.COMPLETE,
            userid=bootstrap_input,
            provider_error_class=None,
            provider_error_code=None,
            terminal_reason=ContentActivityScanTerminalReason.SINGLE_RESPONSE_COMPLETE,
            request_count=1,
        )

    async def observe_posted_notes(self, *, verified_userid: str) -> XhsActivityAttempt:
        self.calls.append(verified_userid)
        try:
            return self.attempts_by_userid[verified_userid].popleft()
        except (KeyError, IndexError) as error:  # pragma: no cover - test setup guard
            raise AssertionError("unexpected XHS provider call") from error

    async def aclose(self) -> None:
        return None


class DelayedByteStream(httpx.AsyncByteStream):
    """A local, credential-free slow stream for the real adapter runtime gate."""

    def __init__(self, chunks: tuple[bytes, ...], *, delay_seconds: float) -> None:
        self._chunks = chunks
        self._delay_seconds = delay_seconds
        self.closed = False
        self.yielded_chunks = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            await asyncio.sleep(self._delay_seconds)
            self.yielded_chunks += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _enabled_settings(
    database_url: URL,
    *,
    provider_max_calls_per_run: int = 1,
    refresh_max_attempts: int = 3,
) -> Settings:
    """Enable only the in-process test double; never construct an HTTP client."""

    return Settings(
        app_env="test",
        database_url=database_url.render_as_string(hide_password=False),
        content_activity_enabled=True,
        content_activity_xhs_enabled=True,
        content_activity_provider_governance_approved=True,
        content_activity_provider_max_calls_per_run=provider_max_calls_per_run,
        content_activity_refresh_max_attempts=refresh_max_attempts,
        tikhub_base_url="https://unit-test.invalid",
        tikhub_api_key="test-only-key",
        _env_file=None,
    )


async def _actor(session: AsyncSession) -> AuthContext:
    department = Department(
        name=f"Content activity runtime {uuid4().hex}",
        password_hash="not-used-by-runtime-test",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name="Content activity runtime operator",
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    session.add_all(
        (
            operator,
            DepartmentPermission(department_id=department.id, role=Role.OPERATOR),
        )
    )
    await session.flush()
    auth_session = AuthSession(
        department_id=department.id,
        operator_id=operator.id,
        token_hash=uuid4().hex * 2,
        csrf_token_hash=uuid4().hex * 2,
        ip="192.0.2.42",
        user_agent="content-activity-runtime-postgres-test",
        expires_at=RUN_AS_OF + timedelta(days=1),
        revoked_at=None,
    )
    session.add(auth_session)
    await session.flush()
    return AuthContext(
        department=department,
        operator=operator,
        role=Role.OPERATOR,
        auth_session=auth_session,
    )


async def _seed_xhs_account(
    session: AsyncSession,
    *,
    label: str,
    userid: str,
    owner_id: UUID | None = None,
) -> InfluencerPlatformAccount:
    influencer = Influencer(
        display_name=f"Content activity {label}",
        owner_operator_id=owner_id,
        crm_stage=CRMStage.TO_DEVELOP,
        status=InfluencerStatus.ACTIVE,
        deleted_at=None,
    )
    session.add(influencer)
    await session.flush()
    account = InfluencerPlatformAccount(
        influencer_id=influencer.id,
        platform=Platform.XIAOHONGSHU,
        # D1A's existing import field is only an exact resolver cross-check;
        # matching this fixture makes the resolver path prove that invariant.
        platform_account_id=userid,
        account_name=f"Content activity {label}",
        account_handle=f"content-activity-{label}",
        profile_url=f"https://example.invalid/content-activity/{label}",
        normalized_profile_url=f"https://example.invalid/content-activity/{label}",
        source=DataSource.MANUAL,
        source_tags=[],
        is_active=True,
    )
    session.add(account)
    await session.flush()
    return account


def _publication_attempt(
    *, observed_at: datetime, published_at: datetime, note_id: str
) -> XhsActivityAttempt:
    return XhsActivityAttempt(
        observation_status=ContentActivityObservationStatus.COMPLETE,
        coverage_status=ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
        activity_result=ContentActivityResult.PUBLICATION_FOUND,
        observed_at=observed_at,
        last_publication_at=published_at,
        latest_publication_id=note_id,
        latest_publication_type=ContentActivityPublicationType.VIDEO,
        co_latest_publication_count=1,
        provider_error_class=None,
        provider_error_code=None,
        terminal_reason=ContentActivityScanTerminalReason.SINGLE_RESPONSE_COMPLETE,
        item_count=1,
        request_count=1,
    )


def _empty_attempt(*, observed_at: datetime) -> XhsActivityAttempt:
    return XhsActivityAttempt(
        observation_status=ContentActivityObservationStatus.COMPLETE,
        coverage_status=ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET,
        activity_result=ContentActivityResult.NO_PUBLIC_CONTENT,
        observed_at=observed_at,
        last_publication_at=None,
        latest_publication_id=None,
        latest_publication_type=None,
        co_latest_publication_count=None,
        provider_error_class=None,
        provider_error_code=None,
        terminal_reason=ContentActivityScanTerminalReason.SINGLE_RESPONSE_COMPLETE,
        item_count=0,
        request_count=1,
    )


def _failure_attempt(*, observed_at: datetime) -> XhsActivityAttempt:
    return XhsActivityAttempt(
        observation_status=ContentActivityObservationStatus.PROVIDER_ERROR,
        coverage_status=ContentActivityCoverageStatus.UNKNOWN,
        activity_result=ContentActivityResult.UNDETERMINED,
        observed_at=observed_at,
        last_publication_at=None,
        latest_publication_id=None,
        latest_publication_type=None,
        co_latest_publication_count=None,
        provider_error_class=ContentActivityProviderErrorClass.MALFORMED_RESPONSE,
        provider_error_code="RESPONSE_INVALID",
        terminal_reason=ContentActivityScanTerminalReason.PROVIDER_FAILURE,
        item_count=0,
        request_count=1,
    )


def _incomplete_attempt(*, observed_at: datetime) -> XhsActivityAttempt:
    return XhsActivityAttempt(
        observation_status=ContentActivityObservationStatus.RESULT_INCOMPLETE,
        coverage_status=ContentActivityCoverageStatus.INCOMPLETE,
        activity_result=ContentActivityResult.UNDETERMINED,
        observed_at=observed_at,
        last_publication_at=None,
        latest_publication_id=None,
        latest_publication_type=None,
        co_latest_publication_count=None,
        provider_error_class=None,
        provider_error_code=None,
        terminal_reason=ContentActivityScanTerminalReason.HAS_MORE_TRUE,
        item_count=1,
        request_count=1,
    )


async def _bind_identity(
    service: ContentActivityService,
    *,
    account: InfluencerPlatformAccount,
    key: str,
    operator_id: UUID | None = None,
    department_id: UUID | None = None,
) -> None:
    assert account.platform_account_id is not None
    result = await service.resolve_xhs_identity(
        platform_account_id=account.id,
        bootstrap_input=account.platform_account_id,
        idempotency_key=f"identity-{key}",
        operator_id=operator_id,
        department_id=department_id,
        ip="192.0.2.42",
        user_agent="content-activity-runtime-postgres-test",
    )
    assert result.outcome == "VERIFIED_CURRENT"
    assert result.identity_binding_id is not None
    assert result.verification_event_id is not None


async def _request_and_process(
    service: ContentActivityService,
    *,
    account: InfluencerPlatformAccount,
    key: str,
    operator_id: UUID | None = None,
    department_id: UUID | None = None,
) -> UUID:
    created = await service.create_xhs_refresh_requests(
        platform_account_ids=(account.id,),
        idempotency_key=f"refresh-{key}",
        operator_id=operator_id,
        department_id=department_id,
        ip="192.0.2.42",
        user_agent="content-activity-runtime-postgres-test",
    )
    assert len(created) == 1
    processed = await service.process_refresh_request(created[0].request_token)
    assert processed.claimed
    assert processed.state is ContentActivityRefreshRequestState.SUCCEEDED
    assert processed.observation_id is not None
    return processed.observation_id


async def _projection(session: AsyncSession, account_id: UUID) -> ContentActivityProjection:
    projection = (
        await session.execute(
            select(ContentActivityProjection)
            .where(ContentActivityProjection.platform_account_id == account_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return projection


def test_postgres_service_persists_projection_transition_contract(
    runtime_database_url: URL,
) -> None:
    """A real migrated database keeps trusted and latest-attempt state separate."""

    async def scenario() -> None:
        engine = create_async_engine(runtime_database_url, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                userid = f"runtime-transition-{uuid4().hex}"
                account = await _seed_xhs_account(
                    session,
                    label="projection-transition",
                    userid=userid,
                )
                await session.commit()

                first_published = RUN_AS_OF - timedelta(days=10)
                backward_published = RUN_AS_OF - timedelta(days=90)
                client = ScriptedXhsClient(
                    {
                        userid: deque(
                            (
                                _publication_attempt(
                                    observed_at=RUN_AS_OF,
                                    published_at=first_published,
                                    note_id="first-note",
                                ),
                                _failure_attempt(observed_at=RUN_AS_OF + timedelta(minutes=1)),
                                _publication_attempt(
                                    observed_at=RUN_AS_OF + timedelta(minutes=2),
                                    published_at=backward_published,
                                    note_id="backward-note",
                                ),
                                _empty_attempt(observed_at=RUN_AS_OF + timedelta(minutes=3)),
                            )
                        )
                    }
                )
                service = ContentActivityService(
                    session,
                    _enabled_settings(runtime_database_url),
                    client=cast(TikHubXhsClient, client),
                    clock=IncrementingClock(RUN_AS_OF),
                )
                await _bind_identity(service, account=account, key="transition")

                first_observation_id = await _request_and_process(
                    service,
                    account=account,
                    key="transition-1",
                )
                first_projection = await _projection(session, account.id)
                assert first_projection.trusted_observation_id == first_observation_id
                assert first_projection.last_publication_at == first_published

                failure_observation_id = await _request_and_process(
                    service,
                    account=account,
                    key="transition-2",
                )
                after_failure = await _projection(session, account.id)
                assert after_failure.latest_attempt_observation_id == failure_observation_id
                assert (
                    after_failure.latest_attempt_observation_status
                    is ContentActivityObservationStatus.PROVIDER_ERROR
                )
                assert after_failure.trusted_observation_id == first_observation_id
                assert after_failure.last_publication_at == first_published

                backward_observation_id = await _request_and_process(
                    service,
                    account=account,
                    key="transition-3",
                )
                after_backward = await _projection(session, account.id)
                assert after_backward.trusted_observation_id == backward_observation_id
                assert after_backward.last_publication_at == backward_published
                assert after_backward.latest_publication_id == "backward-note"

                empty_observation_id = await _request_and_process(
                    service,
                    account=account,
                    key="transition-4",
                )
                after_empty = await _projection(session, account.id)
                assert after_empty.latest_attempt_observation_id == empty_observation_id
                assert after_empty.trusted_observation_id == empty_observation_id
                assert (
                    after_empty.trusted_activity_result is ContentActivityResult.NO_PUBLIC_CONTENT
                )
                assert after_empty.last_publication_at is None
                assert after_empty.latest_publication_id is None
                assert client.calls == [userid, userid, userid, userid]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_provider_deadline_settles_request_and_releases_resources(
    runtime_database_url: URL,
) -> None:
    """A real slow stream leaves no stuck lock, lease, slot, or trusted overwrite."""

    async def scenario() -> None:
        engine = create_async_engine(runtime_database_url, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        http_client: httpx.AsyncClient | None = None
        try:
            async with factory() as session:
                userid = f"runtime-deadline-{uuid4().hex}"
                account = await _seed_xhs_account(
                    session,
                    label="provider-deadline",
                    userid=userid,
                )
                await session.commit()

                settings = _enabled_settings(runtime_database_url, refresh_max_attempts=1)
                initial_client = ScriptedXhsClient(
                    {
                        userid: deque(
                            (
                                _publication_attempt(
                                    observed_at=RUN_AS_OF,
                                    published_at=RUN_AS_OF - timedelta(days=10),
                                    note_id="before-deadline-timeout",
                                ),
                            )
                        )
                    }
                )
                service = ContentActivityService(
                    session,
                    settings,
                    client=cast(TikHubXhsClient, initial_client),
                    clock=IncrementingClock(RUN_AS_OF),
                )
                await _bind_identity(service, account=account, key="provider-deadline")
                trusted_observation_id = await _request_and_process(
                    service,
                    account=account,
                    key="provider-deadline-trusted",
                )
                trusted_projection = await _projection(session, account.id)
                trusted_publication_at = trusted_projection.last_publication_at

                deadline_body = b'{"code":200,"data":{"data":{"has_more":false,"notes":[]}}}'
                chunk_size = max(1, (len(deadline_body) + 9) // 10)
                stream = DelayedByteStream(
                    tuple(
                        deadline_body[index : index + chunk_size]
                        for index in range(0, len(deadline_body), chunk_size)
                    ),
                    delay_seconds=0.06,
                )

                def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(200, request=request, stream=stream)

                http_client = httpx.AsyncClient(
                    base_url="https://tikhub.test",
                    transport=httpx.MockTransport(handler),
                )
                deadline_client = TikHubXhsClient(
                    Settings(
                        **{
                            **settings.model_dump(),
                            "content_activity_http_connect_timeout_seconds": 0.05,
                            "content_activity_http_read_timeout_seconds": 0.15,
                            "content_activity_http_pool_timeout_seconds": 0.05,
                            "content_activity_http_total_timeout_seconds": 0.20,
                        }
                    ),
                    transport=HttpxAsyncTransport("https://tikhub.test", client=http_client),
                    clock=lambda: RUN_AS_OF,
                )
                service.client = deadline_client

                timeout_request = (
                    await service.create_xhs_refresh_requests(
                        platform_account_ids=(account.id,),
                        idempotency_key="provider-deadline-timeout",
                        operator_id=None,
                        department_id=None,
                        ip="192.0.2.42",
                        user_agent="content-activity-runtime-postgres-test",
                    )
                )[0]
                started_at = asyncio.get_running_loop().time()
                timed_out = await service.process_refresh_request(timeout_request.request_token)
                elapsed = asyncio.get_running_loop().time() - started_at

                assert timed_out.claimed
                assert timed_out.state is ContentActivityRefreshRequestState.FAILED
                assert timed_out.observation_id is not None
                assert elapsed >= 0.15
                assert elapsed < 0.50
                assert stream.closed
                assert 0 < stream.yielded_chunks < 10

                after_timeout = await _projection(session, account.id)
                assert after_timeout.latest_attempt_observation_id == timed_out.observation_id
                assert (
                    after_timeout.latest_attempt_observation_status
                    is ContentActivityObservationStatus.PROVIDER_ERROR
                )
                assert (
                    after_timeout.latest_attempt_provider_error_class
                    is ContentActivityProviderErrorClass.TIMEOUT
                )
                assert after_timeout.latest_attempt_provider_error_code == "TRANSPORT_TIMEOUT"
                assert after_timeout.trusted_observation_id == trusted_observation_id
                assert after_timeout.last_publication_at == trusted_publication_at

                persisted_request = await session.scalar(
                    select(ContentActivityRefreshRequest)
                    .where(
                        ContentActivityRefreshRequest.request_token == timeout_request.request_token
                    )
                    .execution_options(populate_existing=True)
                )
                assert persisted_request is not None
                assert persisted_request.state is ContentActivityRefreshRequestState.FAILED
                assert persisted_request.lease_expires_at is None
                assert persisted_request.finished_at is not None
                assert persisted_request.last_error_code == "TRANSPORT_TIMEOUT"

                # A separate transaction can immediately take the same account
                # lock, proving the timed-out provider scope did not retain it.
                async with factory() as contender:
                    locked_account = await asyncio.wait_for(
                        contender.scalar(
                            select(InfluencerPlatformAccount)
                            .where(InfluencerPlatformAccount.id == account.id)
                            .with_for_update()
                        ),
                        timeout=1,
                    )
                    assert locked_account is not None
                    await contender.rollback()

                # The terminal request is no longer active. A new refresh of the
                # same account acquires the global provider slot and succeeds,
                # which proves slot release and reconcile-safe request state.
                recovery_client = ScriptedXhsClient(
                    {
                        userid: deque(
                            (
                                _publication_attempt(
                                    observed_at=RUN_AS_OF + timedelta(minutes=1),
                                    published_at=RUN_AS_OF - timedelta(days=20),
                                    note_id="after-deadline-timeout",
                                ),
                            )
                        )
                    }
                )
                service.client = cast(TikHubXhsClient, recovery_client)
                recovery_request = (
                    await service.create_xhs_refresh_requests(
                        platform_account_ids=(account.id,),
                        idempotency_key="provider-deadline-recovery",
                        operator_id=None,
                        department_id=None,
                        ip="192.0.2.42",
                        user_agent="content-activity-runtime-postgres-test",
                    )
                )[0]
                recovered = await service.process_refresh_request(recovery_request.request_token)
                assert recovered.claimed
                assert recovered.state is ContentActivityRefreshRequestState.SUCCEEDED
                assert recovered.observation_id is not None
        finally:
            if http_client is not None:
                await http_client.aclose()
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_equal_time_conflict_becomes_latest_untrusted_attempt(
    runtime_database_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A quarantined equal-time conflict cannot remain current anywhere."""

    async def scenario() -> None:
        engine = create_async_engine(runtime_database_url, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session)
                assert context.operator is not None
                userid = f"runtime-equal-conflict-{uuid4().hex}"
                account = await _seed_xhs_account(
                    session,
                    label="equal-conflict",
                    userid=userid,
                    owner_id=context.operator.id,
                )
                await session.commit()
                client = ScriptedXhsClient(
                    {
                        userid: deque(
                            (
                                _publication_attempt(
                                    observed_at=RUN_AS_OF,
                                    published_at=RUN_AS_OF - timedelta(days=60),
                                    note_id="equal-first-note",
                                ),
                                _publication_attempt(
                                    observed_at=RUN_AS_OF,
                                    published_at=RUN_AS_OF - timedelta(days=10),
                                    note_id="equal-conflicting-note",
                                ),
                            )
                        )
                    }
                )
                service = ContentActivityService(
                    session,
                    _enabled_settings(runtime_database_url),
                    client=cast(TikHubXhsClient, client),
                    clock=lambda: RUN_AS_OF,
                )
                await _bind_identity(
                    service,
                    account=account,
                    key="equal-conflict",
                    operator_id=context.operator.id,
                    department_id=context.department.id,
                )
                trusted_observation_id = await _request_and_process(
                    service,
                    account=account,
                    key="equal-conflict-first",
                    operator_id=context.operator.id,
                    department_id=context.department.id,
                )
                quarantined_observation_id = await _request_and_process(
                    service,
                    account=account,
                    key="equal-conflict-second",
                    operator_id=context.operator.id,
                    department_id=context.department.id,
                )
                projection = await _projection(session, account.id)
                assert projection.trusted_observation_id == trusted_observation_id
                assert projection.latest_attempt_observation_id == quarantined_observation_id
                assert (
                    projection.latest_attempt_observation_status
                    is ContentActivityObservationStatus.RESULT_UNTRUSTED
                )
                assert projection.latest_attempt_provider_error_code == "EQUAL_OBSERVED_AT_CONFLICT"
                assert projection.last_publication_at == RUN_AS_OF - timedelta(days=60)

                records, total = await InfluencerRepository(session).list_influencers(
                    InfluencerListQuery(
                        content_activity_filter=ContentActivityFilter.INACTIVE_60D,
                        page=1,
                        page_size=20,
                    ),
                    as_of=RUN_AS_OF,
                    content_activity_freshness_policy=(
                        ContentActivityFreshnessPolicy.from_day_threshold(7)
                    ),
                )
                assert total == 0
                assert records == []

                candidate_service = CandidatePoolService(
                    session,
                    freshness_policy=FreshnessPolicy(),
                    content_activity_freshness_policy=(
                        ContentActivityFreshnessPolicy.from_day_threshold(7)
                    ),
                )
                pool = await candidate_service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Equal-time content activity conflict",
                        kind="POTENTIAL_SELLER",
                        policy=SellerTargetingPolicy(
                            content_activity=ContentActivityConstraint(minimum_inactive_days=60)
                        ),
                    ),
                    idempotency_key="equal-time-content-activity-pool",
                )
                import backend_core.growth.service as growth_service_module

                monkeypatch.setattr(growth_service_module, "_utc_now", lambda: RUN_AS_OF)
                run = await candidate_service.reserve_run(
                    context,
                    pool_id=pool.id,
                    idempotency_key="equal-time-content-activity-run",
                )
                completed = await candidate_service.materialize_run(run.id)
                assert completed is not None
                assert completed.match_count == 0
                assert completed.unknown_count == 1
                assert completed.not_match_count == 0

                unknowns = await candidate_service.list_run_members(
                    context,
                    pool_id=pool.id,
                    run_id=run.id,
                    cursor=None,
                    limit=20,
                    result=CandidateResult.UNKNOWN,
                )
                assert len(unknowns.items) == 1
                assert unknowns.items[0].reason_codes == (
                    TargetingReasonCode.CONTENT_ACTIVITY_UNTRUSTED,
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_refresh_batch_is_bounded_and_account_scoped(
    runtime_database_url: URL,
) -> None:
    """The durable XHS batch path is real, bounded, and rejects unsafe scope."""

    async def scenario() -> None:
        engine = create_async_engine(runtime_database_url, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                first = await _seed_xhs_account(
                    session,
                    label="batch-first",
                    userid=f"runtime-batch-first-{uuid4().hex}",
                )
                second = await _seed_xhs_account(
                    session,
                    label="batch-second",
                    userid=f"runtime-batch-second-{uuid4().hex}",
                )
                inactive = await _seed_xhs_account(
                    session,
                    label="batch-inactive",
                    userid=f"runtime-batch-inactive-{uuid4().hex}",
                )
                inactive.is_active = False
                await session.commit()

                service = ContentActivityService(
                    session,
                    _enabled_settings(runtime_database_url, provider_max_calls_per_run=2),
                    client=cast(TikHubXhsClient, ScriptedXhsClient({})),
                    clock=IncrementingClock(RUN_AS_OF),
                )
                created = await service.create_xhs_refresh_requests(
                    platform_account_ids=(first.id, second.id),
                    idempotency_key="runtime-bounded-batch",
                    operator_id=None,
                    department_id=None,
                    ip="192.0.2.42",
                    user_agent="content-activity-runtime-postgres-test",
                )
                assert [request.platform_account_id for request in created] == [
                    first.id,
                    second.id,
                ]

                single_call_service = ContentActivityService(
                    session,
                    _enabled_settings(runtime_database_url),
                    client=cast(TikHubXhsClient, ScriptedXhsClient({})),
                    clock=IncrementingClock(RUN_AS_OF),
                )
                with pytest.raises(ContentActivityError) as batch_error:
                    await single_call_service.create_xhs_refresh_requests(
                        platform_account_ids=(first.id, second.id),
                        idempotency_key="runtime-over-batch",
                        operator_id=None,
                        department_id=None,
                        ip="192.0.2.42",
                        user_agent="content-activity-runtime-postgres-test",
                    )
                assert batch_error.value.code == "REFRESH_BATCH_INVALID"

                with pytest.raises(ContentActivityError) as inactive_error:
                    await single_call_service.create_xhs_refresh_requests(
                        platform_account_ids=(inactive.id,),
                        idempotency_key="runtime-inactive-account",
                        operator_id=None,
                        department_id=None,
                        ip="192.0.2.42",
                        user_agent="content-activity-runtime-postgres-test",
                    )
                assert inactive_error.value.code == "XHS_ACCOUNT_REQUIRED"

                with pytest.raises(ContentActivityError) as missing_error:
                    await single_call_service.create_xhs_refresh_requests(
                        platform_account_ids=(uuid4(),),
                        idempotency_key="runtime-missing-account",
                        operator_id=None,
                        department_id=None,
                        ip="192.0.2.42",
                        user_agent="content-activity-runtime-postgres-test",
                    )
                assert missing_error.value.code == "PLATFORM_ACCOUNT_NOT_FOUND"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_identity_supersession_invalidates_prior_trusted_projection(
    runtime_database_url: URL,
) -> None:
    """A replacement userid cannot inherit a prior userid's trusted activity."""

    async def scenario() -> None:
        engine = create_async_engine(runtime_database_url, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                original_userid = f"runtime-original-{uuid4().hex}"
                replacement_userid = f"runtime-replacement-{uuid4().hex}"
                account = await _seed_xhs_account(
                    session,
                    label="identity-supersession",
                    userid=original_userid,
                )
                await session.commit()

                client = ScriptedXhsClient(
                    {
                        original_userid: deque(
                            (
                                _publication_attempt(
                                    observed_at=RUN_AS_OF,
                                    published_at=RUN_AS_OF - timedelta(days=20),
                                    note_id="original-userid-note",
                                ),
                            )
                        ),
                    }
                )
                service = ContentActivityService(
                    session,
                    _enabled_settings(runtime_database_url),
                    client=cast(TikHubXhsClient, client),
                    clock=IncrementingClock(RUN_AS_OF),
                )
                identity_result = await service.resolve_xhs_identity(
                    platform_account_id=account.id,
                    bootstrap_input=original_userid,
                    idempotency_key="identity-supersession-original",
                    operator_id=None,
                    department_id=None,
                    ip="192.0.2.42",
                    user_agent="content-activity-runtime-postgres-test",
                )
                assert identity_result.identity_binding_id is not None
                original_observation_id = await _request_and_process(
                    service,
                    account=account,
                    key="identity-supersession-original",
                )
                original_binding = await session.get(
                    ProviderAccountIdentity,
                    identity_result.identity_binding_id,
                )
                assert original_binding is not None

                replacement, _ = await service.supersede_xhs_identity(
                    platform_account_id=account.id,
                    expected_current_identity_id=original_binding.id,
                    expected_current_lock_version=original_binding.lock_version,
                    bootstrap_input=replacement_userid,
                    idempotency_key="identity-supersession-replacement",
                    operator_id=None,
                    department_id=None,
                    ip="192.0.2.42",
                    user_agent="content-activity-runtime-postgres-test",
                )

                projection = await _projection(session, account.id)
                expired_binding = await session.get(ProviderAccountIdentity, original_binding.id)
                assert expired_binding is not None
                assert expired_binding.verification_state.value == "SUPERSEDED"
                assert replacement.opaque_external_identity == replacement_userid
                # The immutable original observation remains the latest
                # diagnostic, but cannot remain trusted after its exact binding
                # is no longer VERIFIED_CURRENT.
                assert projection.latest_attempt_observation_id == original_observation_id
                assert projection.trusted_observation_id is None
                assert projection.trusted_observed_at is None
                assert projection.trusted_activity_result is None
                assert projection.last_publication_at is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_candidate_materialization_uses_run_as_of_and_fails_closed(
    runtime_database_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Candidate and list cutoffs consume only fresh, complete trusted evidence."""

    async def scenario() -> None:
        engine = create_async_engine(runtime_database_url, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                context = await _actor(session)
                assert context.operator is not None
                account_specs = {
                    "match": f"runtime-match-{uuid4().hex}",
                    "stale": f"runtime-stale-{uuid4().hex}",
                    "incomplete": f"runtime-incomplete-{uuid4().hex}",
                    "empty": f"runtime-empty-{uuid4().hex}",
                }
                accounts = {
                    label: await _seed_xhs_account(
                        session,
                        label=label,
                        userid=userid,
                        owner_id=context.operator.id,
                    )
                    for label, userid in account_specs.items()
                }
                await session.commit()

                client = ScriptedXhsClient(
                    {
                        account_specs["match"]: deque(
                            (
                                _publication_attempt(
                                    observed_at=RUN_AS_OF - timedelta(days=1),
                                    # Exact equality proves that every cut-off is
                                    # inclusive and computed from run.as_of.
                                    published_at=RUN_AS_OF - timedelta(days=60),
                                    note_id="match-note",
                                ),
                            )
                        ),
                        account_specs["stale"]: deque(
                            (
                                _publication_attempt(
                                    observed_at=RUN_AS_OF - timedelta(days=8),
                                    published_at=RUN_AS_OF - timedelta(days=90),
                                    note_id="stale-note",
                                ),
                            )
                        ),
                        account_specs["incomplete"]: deque(
                            (_incomplete_attempt(observed_at=RUN_AS_OF - timedelta(days=1)),)
                        ),
                        account_specs["empty"]: deque(
                            (_empty_attempt(observed_at=RUN_AS_OF - timedelta(days=1)),)
                        ),
                    }
                )
                settings = _enabled_settings(runtime_database_url)
                clock_starts = {
                    "match": RUN_AS_OF - timedelta(days=1),
                    "stale": RUN_AS_OF - timedelta(days=8),
                    "incomplete": RUN_AS_OF - timedelta(days=1),
                    "empty": RUN_AS_OF - timedelta(days=1),
                }
                for label, account in accounts.items():
                    service = ContentActivityService(
                        session,
                        settings,
                        client=cast(TikHubXhsClient, client),
                        clock=IncrementingClock(clock_starts[label]),
                    )
                    await _bind_identity(
                        service,
                        account=account,
                        key=f"candidate-{label}",
                        operator_id=context.operator.id,
                        department_id=context.department.id,
                    )
                    await _request_and_process(
                        service,
                        account=account,
                        key=f"candidate-{label}",
                        operator_id=context.operator.id,
                        department_id=context.department.id,
                    )

                candidate_service = CandidatePoolService(
                    session,
                    freshness_policy=FreshnessPolicy(),
                    content_activity_freshness_policy=(
                        ContentActivityFreshnessPolicy.from_day_threshold(7)
                    ),
                )
                pool = await candidate_service.create_pool(
                    context,
                    CandidatePoolCreateInput(
                        name="Content Activity current-public targeting",
                        kind="POTENTIAL_SELLER",
                        policy=SellerTargetingPolicy(
                            content_activity=ContentActivityConstraint(minimum_inactive_days=60)
                        ),
                    ),
                    idempotency_key="content-activity-runtime-pool",
                )

                # Capture a fixed run instant, then deliberately advance the
                # wall-clock helper before materialization. Results must remain
                # deterministic from the persisted ``run.as_of`` value.
                import backend_core.growth.service as growth_service_module

                monkeypatch.setattr(growth_service_module, "_utc_now", lambda: RUN_AS_OF)
                run = await candidate_service.reserve_run(
                    context,
                    pool_id=pool.id,
                    idempotency_key="content-activity-runtime-run",
                )
                assert run.as_of == RUN_AS_OF

                # Simulate a newer trusted provider observation arriving after
                # reservation but before the worker materializes the run. A
                # mutable present-tense projection now points at this recent
                # publication; the reserved run must instead reconstruct the
                # earlier as-of fact and keep its deterministic MATCH.
                client.attempts_by_userid[account_specs["match"]].append(
                    _publication_attempt(
                        observed_at=RUN_AS_OF + timedelta(days=1),
                        published_at=RUN_AS_OF - timedelta(days=10),
                        note_id="post-as-of-recent-note",
                    )
                )
                post_as_of_service = ContentActivityService(
                    session,
                    settings,
                    client=cast(TikHubXhsClient, client),
                    clock=IncrementingClock(RUN_AS_OF + timedelta(days=1)),
                )
                await _request_and_process(
                    post_as_of_service,
                    account=accounts["match"],
                    key="candidate-match-post-as-of",
                    operator_id=context.operator.id,
                    department_id=context.department.id,
                )
                current_projection = await _projection(session, accounts["match"].id)
                assert current_projection.trusted_observed_at is not None
                assert current_projection.trusted_observed_at > run.as_of
                assert current_projection.last_publication_at == RUN_AS_OF - timedelta(days=10)

                monkeypatch.setattr(
                    growth_service_module,
                    "_utc_now",
                    lambda: RUN_AS_OF + timedelta(days=365),
                )
                activity_history_statements: list[str] = []

                def track_activity_history_query(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    _parameters: object,
                    _context: object,
                    _executemany: object,
                ) -> None:
                    if "content_activity_observations" in statement:
                        activity_history_statements.append(statement)

                event.listen(
                    engine.sync_engine,
                    "before_cursor_execute",
                    track_activity_history_query,
                )
                try:
                    completed = await candidate_service.materialize_run(run.id)
                finally:
                    event.remove(
                        engine.sync_engine,
                        "before_cursor_execute",
                        track_activity_history_query,
                    )
                assert completed is not None
                assert completed.as_of == RUN_AS_OF
                assert completed.match_count == 1
                assert completed.unknown_count == 3
                assert completed.not_match_count == 0
                # Two history reads (latest attempt + trusted-current) cover
                # the whole Candidate batch. A per-account observation lookup
                # would make this grow with the four accounts above.
                assert len(activity_history_statements) == 2

                matches = await candidate_service.list_run_members(
                    context,
                    pool_id=pool.id,
                    run_id=run.id,
                    cursor=None,
                    limit=20,
                    result=CandidateResult.MATCH,
                )
                assert [member.platform_account_id for member in matches.items] == [
                    accounts["match"].id
                ]
                assert matches.items[0].reason_codes == (
                    TargetingReasonCode.CONTENT_ACTIVITY_MATCH,
                )

                unknowns = await candidate_service.list_run_members(
                    context,
                    pool_id=pool.id,
                    run_id=run.id,
                    cursor=None,
                    limit=20,
                    result=CandidateResult.UNKNOWN,
                )
                assert {member.reason_codes for member in unknowns.items} == {
                    (TargetingReasonCode.CONTENT_ACTIVITY_STALE,),
                    (TargetingReasonCode.CONTENT_ACTIVITY_INCOMPLETE,),
                    (TargetingReasonCode.CONTENT_ACTIVITY_NO_PUBLIC_CONTENT,),
                }

                # Library reads are intentionally present-tense. At the old
                # read ``as_of`` the current projection is future evidence, so
                # it must be excluded rather than falling back to a historical
                # publication. Candidate Runs above are the separate
                # reproducible-as-of path.
                list_statements: list[str] = []

                def track_list_query(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    _parameters: object,
                    _context: object,
                    _executemany: object,
                ) -> None:
                    if statement.lstrip().upper().startswith("SELECT"):
                        list_statements.append(statement)

                event.listen(engine.sync_engine, "before_cursor_execute", track_list_query)
                try:
                    records, total = await InfluencerRepository(session).list_influencers(
                        InfluencerListQuery(
                            content_activity_filter=ContentActivityFilter.INACTIVE_60D,
                            page=1,
                            page_size=20,
                        ),
                        as_of=RUN_AS_OF,
                        content_activity_freshness_policy=(
                            ContentActivityFreshnessPolicy.from_day_threshold(7)
                        ),
                    )
                finally:
                    event.remove(engine.sync_engine, "before_cursor_execute", track_list_query)
                assert total == 0
                assert records == []
                # The query remains a fixed finite set even as the repository
                # considers every account; no Content Activity N+1 read occurs.
                assert len(list_statements) == 2
                assert client.calls == [
                    account_specs["match"],
                    account_specs["stale"],
                    account_specs["incomplete"],
                    account_specs["empty"],
                    account_specs["match"],
                ]
        finally:
            await engine.dispose()

    asyncio.run(scenario())
