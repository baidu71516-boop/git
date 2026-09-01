"""HTTP contract coverage for the Phase 1C read-only influencer API."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.http.dependencies import get_database_session, get_redis
from app.http.influencers import get_influencer_service
from app.main import app
from backend_core.auth.enums import NON_ADMIN_MODULE_KEYS, DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import (
    AuthSession,
    Department,
    DepartmentPermission,
    Operator,
    OperatorModulePermission,
)
from backend_core.auth.security import hash_password, hash_token
from backend_core.config import get_settings
from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityProviderErrorClass,
    ContentActivityPublicationType,
    ContentActivityResult,
)
from backend_core.content_activity.models import ContentActivityProjection
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.influencers.enums import (
    ContactType,
    ContactValidationStatus,
    ContentActivityFilter,
    CRMStage,
    DataSource,
    InfluencerStatus,
    Platform,
)
from backend_core.influencers.freshness import FreshnessStatus
from backend_core.influencers.models import (
    Influencer,
    InfluencerContact,
    InfluencerCurrentMetrics,
    InfluencerMetricSnapshot,
    InfluencerPlatformAccount,
    InfluencerSourceState,
    PlatformAccountSourceIdentity,
)
from backend_core.influencers.service import InfluencerPermissionError
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

NOW = datetime(2026, 8, 11, 4, 0, tzinfo=UTC)
INFLUENCER_ID = UUID("00000000-0000-0000-0000-000000000201")
ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000202")
DISABLED_INFLUENCER_ID = UUID("00000000-0000-0000-0000-000000000203")
DELETED_INFLUENCER_ID = UUID("00000000-0000-0000-0000-000000000204")
EMAIL_FIXTURE = "fixture@example.invalid"
PHONE_FIXTURE = "12345678901"


@dataclass(frozen=True)
class ApiEnvironment:
    session: AsyncSession
    redis: FakeRedis
    tokens: dict[Role, str]
    owner_id: UUID


async def _seed_auth_session(
    session: AsyncSession,
    *,
    role: Role,
    operator_selected: bool,
) -> str:
    department = Department(
        name=f"Influencer HTTP {role.value}",
        password_hash="not-used-by-http-test",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name=f"Selected identity {role.value}",
        role=role,
        status=OperatorStatus.ACTIVE,
        password_hash=(
            hash_password(f"influencer-{role.value}-operator-test-password")
            if operator_selected
            else None
        ),
        credential_version=1 if operator_selected else 0,
    )
    session.add(operator)
    await session.flush()
    token = f"influencer-http-{role.value}"
    auth_session = AuthSession(
        department_id=department.id,
        operator_id=operator.id if operator_selected else None,
        operator_credential_version=1 if operator_selected else None,
        token_hash=hash_token(token),
        csrf_token_hash=hash_token(f"csrf-{role.value}"),
        ip="192.0.2.20",
        user_agent="influencer-http-test",
        expires_at=datetime.now(UTC) + timedelta(hours=12),
        revoked_at=None,
    )
    session.add_all(
        [
            DepartmentPermission(department_id=department.id, role=role),
            auth_session,
        ]
    )
    if role is not Role.SUPER_ADMIN:
        session.add_all(
            OperatorModulePermission(
                operator_id=operator.id,
                department_id=department.id,
                module_key=module_key,
            )
            for module_key in NON_ADMIN_MODULE_KEYS
        )
    await session.flush()
    return token


async def _seed_influencer_graph(session: AsyncSession) -> UUID:
    owner_department = Department(
        name="Influencer Owner Department",
        password_hash="not-used-by-http-test",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(owner_department)
    await session.flush()
    owner = Operator(
        department_id=owner_department.id,
        name="Disabled Cross Department Owner",
        role=Role.MANAGER,
        status=OperatorStatus.DISABLED,
    )
    session.add(owner)
    await session.flush()

    influencer = Influencer(
        id=INFLUENCER_ID,
        display_name="HTTP 脱敏达人",
        owner_operator_id=owner.id,
        crm_stage=CRMStage.HIGH_INTENT,
        status=InfluencerStatus.ACTIVE,
        deleted_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    disabled = Influencer(
        id=DISABLED_INFLUENCER_ID,
        display_name="Disabled HTTP fixture",
        crm_stage=CRMStage.TO_DEVELOP,
        status=InfluencerStatus.DISABLED,
        deleted_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    deleted = Influencer(
        id=DELETED_INFLUENCER_ID,
        display_name="Deleted HTTP fixture",
        crm_stage=CRMStage.TO_DEVELOP,
        status=InfluencerStatus.ACTIVE,
        deleted_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add_all([influencer, disabled, deleted])
    await session.flush()

    account = InfluencerPlatformAccount(
        id=ACCOUNT_ID,
        influencer_id=influencer.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id="http-fixture-account",
        account_name="HTTP 脱敏账号",
        account_handle="http-fixture-handle",
        profile_url="https://example.invalid/profile/http-fixture-account",
        normalized_profile_url="https://example.invalid/profile/http-fixture-account",
        source=DataSource.HUITUN,
        is_active=True,
        bio="公开简介",
        source_tags=["动画", "Beauty"],
    )
    session.add(account)
    await session.flush()
    import_job_id = uuid4()
    import_row_id = uuid4()
    session.add_all(
        [
            InfluencerContact(
                influencer_id=influencer.id,
                platform_account_id=account.id,
                type=ContactType.EMAIL,
                value=EMAIL_FIXTURE,
                normalized_value=EMAIL_FIXTURE,
                source=DataSource.MANUAL,
                validation_status=ContactValidationStatus.UNVERIFIED,
                is_current=True,
                possible_duplicate_contact=True,
                first_seen_at=NOW,
                last_seen_at=NOW,
            ),
            InfluencerContact(
                influencer_id=influencer.id,
                platform_account_id=account.id,
                type=ContactType.PHONE,
                value=PHONE_FIXTURE,
                normalized_value=PHONE_FIXTURE,
                source=DataSource.MANUAL,
                validation_status=ContactValidationStatus.UNVERIFIED,
                is_current=True,
                possible_duplicate_contact=False,
                first_seen_at=NOW,
                last_seen_at=NOW,
            ),
            InfluencerSourceState(
                influencer_id=influencer.id,
                platform_account_id=account.id,
                source=DataSource.HUITUN,
                source_updated_at=NOW,
                source_data={"creator_tags": ["动画"], "private": "not-public"},
                source_data_hash=uuid4().hex * 2,
                state_version=1,
                last_import_job_id=import_job_id,
                last_import_row_id=import_row_id,
            ),
            InfluencerCurrentMetrics(
                influencer_id=influencer.id,
                platform_account_id=account.id,
                source=DataSource.HUITUN,
                source_updated_at=NOW,
                metrics={
                    "followers_count": 100,
                    "notes_7d": 0,
                    "notes_60d": 0,
                    "huitun_score": "88.5",
                },
                metrics_hash=uuid4().hex * 2,
                last_import_job_id=import_job_id,
                last_import_row_id=import_row_id,
            ),
            PlatformAccountSourceIdentity(
                platform_account_id=account.id,
                platform=Platform.XIAOHONGSHU,
                source=DataSource.HUITUN,
                external_account_id="http-provider-fixture",
                first_import_job_id=import_job_id,
                first_import_row_id=import_row_id,
                last_import_job_id=import_job_id,
                last_import_row_id=import_row_id,
            ),
            InfluencerMetricSnapshot(
                influencer_id=influencer.id,
                platform_account_id=account.id,
                source=DataSource.HUITUN,
                source_updated_at=NOW,
                import_job_id=import_job_id,
                import_row_id=import_row_id,
                captured_at=NOW,
                metrics={"followers_count": 100, "huitun_score": "88.5"},
                metrics_hash=uuid4().hex * 2,
                snapshot_key=uuid4().hex * 2,
            ),
        ]
    )
    await session.commit()
    return owner.id


async def _seed_content_activity_projection(
    session: AsyncSession,
    *,
    account: InfluencerPlatformAccount,
    trusted_observed_at: datetime | None,
    last_publication_at: datetime | None = None,
    latest_observed_at: datetime | None = None,
    latest_status: ContentActivityObservationStatus = ContentActivityObservationStatus.COMPLETE,
    latest_coverage: ContentActivityCoverageStatus = (
        ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET
    ),
    latest_result: ContentActivityResult = ContentActivityResult.PUBLICATION_FOUND,
) -> None:
    """Attach only safe projection fields to an HTTP read fixture."""

    has_trusted = trusted_observed_at is not None
    trusted_observation_id = uuid4() if has_trusted else None
    resolved_latest_observed_at = latest_observed_at or trusted_observed_at or NOW
    latest_attempt_observation_id = (
        trusted_observation_id
        if (
            trusted_observation_id is not None
            and resolved_latest_observed_at == trusted_observed_at
            and latest_status is ContentActivityObservationStatus.COMPLETE
            and latest_coverage is ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET
            and latest_result is ContentActivityResult.PUBLICATION_FOUND
        )
        else uuid4()
    )
    has_publication = has_trusted and last_publication_at is not None
    session.add(
        ContentActivityProjection(
            platform_account_id=account.id,
            platform=account.platform,
            latest_attempt_observation_id=latest_attempt_observation_id,
            latest_attempt_observed_at=resolved_latest_observed_at,
            latest_attempt_observation_status=latest_status,
            latest_attempt_coverage_status=latest_coverage,
            latest_attempt_activity_result=latest_result,
            latest_attempt_provider_error_class=(
                ContentActivityProviderErrorClass.MALFORMED_RESPONSE
                if latest_status is ContentActivityObservationStatus.PROVIDER_ERROR
                else None
            ),
            latest_attempt_provider_error_code=(
                "SYNTHETIC_ERROR"
                if latest_status is ContentActivityObservationStatus.PROVIDER_ERROR
                else None
            ),
            trusted_observation_id=trusted_observation_id,
            trusted_observed_at=trusted_observed_at,
            trusted_observation_status=(
                ContentActivityObservationStatus.COMPLETE if has_trusted else None
            ),
            trusted_coverage_status=(
                ContentActivityCoverageStatus.FULL_CURRENT_PUBLIC_SET if has_trusted else None
            ),
            trusted_activity_result=(
                ContentActivityResult.PUBLICATION_FOUND if has_trusted else None
            ),
            trusted_capability_policy_version=(
                "TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1" if has_trusted else None
            ),
            last_publication_at=last_publication_at if has_publication else None,
            latest_publication_id_namespace=("xiaohongshu.noteid" if has_publication else None),
            latest_publication_id="http-activity-note" if has_publication else None,
            latest_publication_type=(
                ContentActivityPublicationType.VIDEO if has_publication else None
            ),
            co_latest_publication_count=1 if has_publication else None,
            version=1,
        )
    )
    await session.flush()


async def _seed_activity_account(
    session: AsyncSession,
    *,
    label: str,
) -> tuple[Influencer, InfluencerPlatformAccount]:
    influencer = Influencer(
        display_name=f"HTTP activity {label}",
        crm_stage=CRMStage.TO_DEVELOP,
        status=InfluencerStatus.ACTIVE,
        deleted_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(influencer)
    await session.flush()
    account = InfluencerPlatformAccount(
        influencer_id=influencer.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id=f"http-activity-{label}",
        account_name=f"HTTP activity {label}",
        account_handle=None,
        profile_url=f"https://example.invalid/activity/{label}",
        normalized_profile_url=f"https://example.invalid/activity/{label}",
        source=DataSource.GENERIC,
        is_active=True,
        source_tags=[],
    )
    session.add(account)
    await session.flush()
    return influencer, account


@asynccontextmanager
async def api_environment() -> AsyncIterator[ApiEnvironment]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    fake_redis = FakeRedis(decode_responses=True)
    async with factory() as session:
        tokens = {
            role: await _seed_auth_session(
                session,
                role=role,
                operator_selected=True,
            )
            for role in Role
        }
        owner_id = await _seed_influencer_graph(session)

        async def override_database_session() -> AsyncIterator[AsyncSession]:
            yield session

        def override_redis() -> FakeRedis:
            return fake_redis

        app.dependency_overrides[get_database_session] = override_database_session
        app.dependency_overrides[get_redis] = override_redis
        try:
            yield ApiEnvironment(
                session=session,
                redis=fake_redis,
                tokens=tokens,
                owner_id=owner_id,
            )
        finally:
            app.dependency_overrides.clear()
    await fake_redis.aclose()
    await engine.dispose()


def authenticated_client(client: AsyncClient, token: str) -> None:
    client.cookies.set(get_settings().session_cookie_name, token)


def assert_success_envelope(response: Response) -> dict[str, object]:
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert isinstance(body["request_id"], str) and body["request_id"]
    return body


def assert_validation_error(response: Response) -> None:
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(body["request_id"], str) and body["request_id"]


def test_registered_endpoints_auth_csrf_contact_and_envelope() -> None:
    async def scenario() -> None:
        async with api_environment() as environment:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                unauthenticated = await client.get("/api/v1/influencers")
                assert unauthenticated.status_code == 401
                assert unauthenticated.json()["error"]["code"] == "AUTH_REQUIRED"

                authenticated_client(client, environment.tokens[Role.VIEWER])
                assert get_settings().csrf_cookie_name not in client.cookies
                listed = await client.get(
                    "/api/v1/influencers",
                    headers={"X-Request-ID": "phase1c-http-request"},
                )
                assert listed.status_code == 200
                assert listed.headers["X-Request-ID"] == "phase1c-http-request"
                list_body = assert_success_envelope(listed)
                assert list_body["request_id"] == "phase1c-http-request"
                list_data = list_body["data"]
                assert isinstance(list_data, dict)
                assert list_data["page"] == 1
                assert list_data["page_size"] == 50
                assert list_data["total"] == 1
                item = list_data["items"][0]
                assert item["freshness_status"] == FreshnessStatus.UNKNOWN.value
                assert item["requires_refresh"] is True
                account_freshness = item["platform_accounts"][0]
                assert account_freshness["last_huitun_observed_at"] is None
                assert account_freshness["last_huitun_imported_at"] is None
                assert account_freshness["freshness_status"] == FreshnessStatus.UNKNOWN.value
                assert account_freshness["freshness_age_days"] is None
                assert account_freshness["requires_refresh"] is True
                contacts = list_data["items"][0]["current_contacts"]
                assert [contact["display_value"] for contact in contacts] == ["***", "***"]
                assert "normalized_value" not in listed.text

                viewer_email = await client.get(
                    "/api/v1/influencers", params={"contact_filter": "has_email"}
                )
                assert viewer_email.status_code == 200
                assert viewer_email.json()["data"]["total"] == 1
                assert (
                    viewer_email.json()["data"]["items"][0]["current_contacts"][0]["display_value"]
                    == "***"
                )
                notes_zero = await client.get(
                    "/api/v1/influencers", params={"notes_60d_filter": "zero"}
                )
                assert notes_zero.status_code == 200
                assert notes_zero.json()["data"]["total"] == 1
                notes_7d_zero = await client.get(
                    "/api/v1/influencers", params={"notes_7d_filter": "zero"}
                )
                assert notes_7d_zero.status_code == 200
                assert notes_7d_zero.json()["data"]["total"] == 1

                options = await client.get("/api/v1/influencers/filter-options")
                assert options.status_code == 200
                options_data = assert_success_envelope(options)["data"]
                assert set(options_data) == {"owners", "tags", "crm_stages"}
                assert options_data["owners"][0]["id"] == str(environment.owner_id)
                assert options_data["owners"][0]["status"] == "disabled"

                detail = await client.get(f"/api/v1/influencers/{INFLUENCER_ID}")
                assert detail.status_code == 200
                detail_data = assert_success_envelope(detail)["data"]
                assert detail_data["freshness_status"] == FreshnessStatus.UNKNOWN.value
                assert detail_data["requires_refresh"] is True
                assert [contact["display_value"] for contact in detail_data["contacts"]] == [
                    "***",
                    "***",
                ]
                assert "normalized_value" not in detail.text

                unknown = await client.get(
                    "/api/v1/influencers",
                    params={"freshness_status": FreshnessStatus.UNKNOWN.value},
                )
                assert unknown.status_code == 200
                assert unknown.json()["data"]["total"] == 1
                refresh_required = await client.get(
                    "/api/v1/influencers", params={"requires_refresh": "true"}
                )
                assert refresh_required.json()["data"]["total"] == 1
                no_refresh = await client.get(
                    "/api/v1/influencers", params={"requires_refresh": "false"}
                )
                assert no_refresh.json()["data"]["total"] == 0
                observed_range = await client.get(
                    "/api/v1/influencers",
                    params={"last_huitun_observed_before": NOW.isoformat()},
                )
                assert observed_range.json()["data"]["total"] == 0

                snapshots = await client.get(
                    f"/api/v1/influencers/{INFLUENCER_ID}/metric-snapshots"
                )
                assert snapshots.status_code == 200
                snapshot_data = assert_success_envelope(snapshots)["data"]
                assert snapshot_data["page"] == 1
                assert snapshot_data["page_size"] == 50
                assert snapshot_data["total"] == 1

                for role in (Role.SUPER_ADMIN, Role.MANAGER, Role.OPERATOR):
                    client.cookies.clear()
                    authenticated_client(client, environment.tokens[role])
                    role_list = await client.get("/api/v1/influencers")
                    assert role_list.status_code == 200
                    role_contacts = role_list.json()["data"]["items"][0]["current_contacts"]
                    assert [contact["display_value"] for contact in role_contacts] == [
                        EMAIL_FIXTURE,
                        PHONE_FIXTURE,
                    ]

    asyncio.run(scenario())


def test_content_activity_read_fields_and_filters_are_fail_closed_at_http_boundary() -> None:
    async def scenario() -> None:
        async with api_environment() as environment:
            now = datetime.now(UTC).replace(microsecond=0)
            inactive_account = await environment.session.get(
                InfluencerPlatformAccount,
                ACCOUNT_ID,
            )
            assert inactive_account is not None
            active, active_account = await _seed_activity_account(
                environment.session,
                label="active",
            )
            unknown, unknown_account = await _seed_activity_account(
                environment.session,
                label="unknown",
            )
            stale, stale_account = await _seed_activity_account(
                environment.session,
                label="stale",
            )
            last_known, last_known_account = await _seed_activity_account(
                environment.session,
                label="last-known",
            )
            await _seed_content_activity_projection(
                environment.session,
                account=inactive_account,
                trusted_observed_at=now - timedelta(days=1),
                last_publication_at=now - timedelta(days=200),
            )
            await _seed_content_activity_projection(
                environment.session,
                account=active_account,
                trusted_observed_at=now - timedelta(days=1),
                last_publication_at=now - timedelta(days=3),
            )
            await _seed_content_activity_projection(
                environment.session,
                account=unknown_account,
                trusted_observed_at=None,
                latest_observed_at=now,
                latest_status=ContentActivityObservationStatus.RESULT_INCOMPLETE,
                latest_coverage=ContentActivityCoverageStatus.INCOMPLETE,
                latest_result=ContentActivityResult.UNDETERMINED,
            )
            await _seed_content_activity_projection(
                environment.session,
                account=stale_account,
                trusted_observed_at=now - timedelta(days=8),
                last_publication_at=now - timedelta(days=200),
            )
            await _seed_content_activity_projection(
                environment.session,
                account=last_known_account,
                trusted_observed_at=now - timedelta(days=1),
                last_publication_at=now - timedelta(days=200),
                latest_observed_at=now,
                latest_status=ContentActivityObservationStatus.PROVIDER_ERROR,
                latest_coverage=ContentActivityCoverageStatus.UNKNOWN,
                latest_result=ContentActivityResult.UNDETERMINED,
            )
            await environment.session.commit()

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                authenticated_client(client, environment.tokens[Role.VIEWER])
                listed = await client.get("/api/v1/influencers")
                assert listed.status_code == 200
                list_data = assert_success_envelope(listed)["data"]
                assert isinstance(list_data, dict)
                by_id = {item["id"]: item for item in list_data["items"]}
                inactive_item = by_id[str(INFLUENCER_ID)]
                inactive_activity = inactive_item["platform_accounts"][0]
                assert inactive_activity["content_activity_state"] == "current"
                assert inactive_activity["content_activity_last_publication_at"] is not None
                assert inactive_activity["content_activity_inactive_days"] >= 200
                assert "provider_raw_response" not in listed.text
                assert "bootstrap_input" not in listed.text
                assert "SYNTHETIC_ERROR" not in listed.text

                detail = await client.get(f"/api/v1/influencers/{INFLUENCER_ID}")
                assert detail.status_code == 200
                detail_account = assert_success_envelope(detail)["data"]["platform_accounts"][0]
                assert detail_account["content_activity_state"] == "current"
                assert detail_account["content_activity_last_publication_at"] is not None
                assert detail_account["content_activity_inactive_days"] >= 200

                active_filter = await client.get(
                    "/api/v1/influencers",
                    params={
                        "content_activity_filter": ContentActivityFilter.ACTIVE_WITHIN_7D.value
                    },
                )
                assert {
                    item["id"] for item in assert_success_envelope(active_filter)["data"]["items"]
                } == {str(active.id)}
                for activity_filter in (
                    ContentActivityFilter.INACTIVE_30D,
                    ContentActivityFilter.INACTIVE_60D,
                    ContentActivityFilter.INACTIVE_90D,
                    ContentActivityFilter.INACTIVE_180D,
                ):
                    filtered = await client.get(
                        "/api/v1/influencers",
                        params={"content_activity_filter": activity_filter.value},
                    )
                    assert {
                        item["id"] for item in assert_success_envelope(filtered)["data"]["items"]
                    } == {str(INFLUENCER_ID)}

                last_known_detail = await client.get(f"/api/v1/influencers/{last_known.id}")
                assert last_known_detail.status_code == 200
                last_known_activity = assert_success_envelope(last_known_detail)["data"][
                    "platform_accounts"
                ][0]
                assert last_known_activity["content_activity_state"] == "last_known"
                assert last_known_activity["content_activity_last_publication_at"] is None
                assert last_known_activity["content_activity_inactive_days"] is None
                assert (
                    last_known_activity["content_activity_latest_attempt_observation_status"]
                    == ContentActivityObservationStatus.PROVIDER_ERROR.value
                )
                assert str(unknown.id) not in {
                    item["id"]
                    for item in assert_success_envelope(
                        await client.get(
                            "/api/v1/influencers",
                            params={
                                "content_activity_filter": (
                                    ContentActivityFilter.INACTIVE_30D.value
                                )
                            },
                        )
                    )["data"]["items"]
                }
                assert str(stale.id) not in {
                    item["id"]
                    for item in assert_success_envelope(
                        await client.get(
                            "/api/v1/influencers",
                            params={
                                "content_activity_filter": (
                                    ContentActivityFilter.INACTIVE_30D.value
                                )
                            },
                        )
                    )["data"]["items"]
                }

    asyncio.run(scenario())


def test_list_query_contract_validation_and_duplicate_parameters() -> None:
    async def scenario() -> None:
        async with api_environment() as environment:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                authenticated_client(client, environment.tokens[Role.OPERATOR])
                valid_requests: list[dict[str, object]] = [
                    {"q": "HTTP"},
                    {"q": "   "},
                    {"tag": "动画"},
                    {"tag": "   "},
                    {"followers_min": "0"},
                    {"owner_operator_id": str(environment.owner_id)},
                    {"crm_stage": CRMStage.HIGH_INTENT.value},
                    {"contact_filter": "has_contact"},
                    {"contact_filter": "has_email"},
                    {"contact_filter": "no_contact"},
                    {"notes_7d_filter": "zero"},
                    {"notes_7d_filter": "one_to_two"},
                    {"notes_7d_filter": "three_plus"},
                    {"notes_7d_filter": "missing"},
                    {"notes_60d_filter": "zero"},
                    {"notes_60d_filter": "one_to_two"},
                    {"notes_60d_filter": "three_to_nine"},
                    {"notes_60d_filter": "ten_or_more"},
                    {"notes_60d_filter": "missing"},
                    {"content_activity_filter": ContentActivityFilter.ACTIVE_WITHIN_7D.value},
                    {"content_activity_filter": ContentActivityFilter.INACTIVE_30D.value},
                    {"content_activity_filter": ContentActivityFilter.INACTIVE_60D.value},
                    {"content_activity_filter": ContentActivityFilter.INACTIVE_90D.value},
                    {"content_activity_filter": ContentActivityFilter.INACTIVE_180D.value},
                    {"freshness_status": FreshnessStatus.UNKNOWN.value},
                    {"requires_refresh": "true"},
                    {"requires_refresh": "false"},
                    {"last_huitun_observed_before": "2026-08-11T12:00:00+08:00"},
                    {"last_huitun_observed_after": "2026-08-10T20:00:00Z"},
                    {
                        "last_huitun_observed_after": "2026-08-10T20:00:00Z",
                        "last_huitun_observed_before": "2026-08-11T12:00:00+08:00",
                    },
                    {"page_size": "100"},
                ]
                for params in valid_requests:
                    response = await client.get("/api/v1/influencers", params=params)
                    assert response.status_code == 200

                invalid_requests: list[dict[str, object]] = [
                    {"unexpected_filter": "ignored-no-longer"},
                    {"q": "名" * 161},
                    {"tag": "T" * 161},
                    {"followers_min": "-1"},
                    {"followers_min": "1.5"},
                    {"followers_min": "2", "followers_max": "1"},
                    {"owner_operator_id": "not-a-uuid"},
                    {"crm_stage": "不存在阶段"},
                    {"contact_filter": "email"},
                    {"notes_7d_filter": "0"},
                    {"notes_60d_filter": "0"},
                    {"content_activity_filter": "inactive_999d"},
                    {"freshness_status": "not-a-status"},
                    {"requires_refresh": "1"},
                    {"requires_refresh": "TRUE"},
                    {"requires_refresh": "yes"},
                    {"last_huitun_observed_before": "2026-08-11T04:00:00"},
                    {"last_huitun_observed_after": "not-a-datetime"},
                    {
                        "last_huitun_observed_after": "2026-08-11T04:00:00Z",
                        "last_huitun_observed_before": "2026-08-11T03:59:59Z",
                    },
                    {"page": "0"},
                    {"page_size": "101"},
                ]
                for params in invalid_requests:
                    assert_validation_error(await client.get("/api/v1/influencers", params=params))

                duplicate_values: dict[str, tuple[str, str]] = {
                    "q": ("a", "b"),
                    "tag": ("a", "b"),
                    "followers_min": ("1", "2"),
                    "followers_max": ("1", "2"),
                    "owner_operator_id": (str(uuid4()), str(uuid4())),
                    "crm_stage": (CRMStage.TO_DEVELOP.value, CRMStage.HIGH_INTENT.value),
                    "contact_filter": ("has_contact", "no_contact"),
                    "notes_7d_filter": ("zero", "missing"),
                    "notes_60d_filter": ("zero", "missing"),
                    "content_activity_filter": (
                        ContentActivityFilter.ACTIVE_WITHIN_7D.value,
                        ContentActivityFilter.INACTIVE_60D.value,
                    ),
                    "freshness_status": (
                        FreshnessStatus.FRESH.value,
                        FreshnessStatus.STALE.value,
                    ),
                    "requires_refresh": ("true", "false"),
                    "last_huitun_observed_before": (
                        "2026-08-11T04:00:00Z",
                        "2026-08-12T04:00:00Z",
                    ),
                    "last_huitun_observed_after": (
                        "2026-08-10T04:00:00Z",
                        "2026-08-11T04:00:00Z",
                    ),
                    "page": ("1", "2"),
                    "page_size": ("50", "100"),
                }
                for name, values in duplicate_values.items():
                    response = await client.get(
                        "/api/v1/influencers",
                        params=[(name, values[0]), (name, values[1])],
                    )
                    assert_validation_error(response)

                default_page = await client.get("/api/v1/influencers")
                assert default_page.json()["data"]["page"] == 1
                assert default_page.json()["data"]["page_size"] == 50
                max_page = await client.get("/api/v1/influencers", params={"page_size": "100"})
                assert max_page.json()["data"]["page_size"] == 100

    asyncio.run(scenario())


def test_detail_snapshot_errors_and_snapshot_query_validation() -> None:
    async def scenario() -> None:
        async with api_environment() as environment:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                authenticated_client(client, environment.tokens[Role.VIEWER])
                for missing_id in (uuid4(), DISABLED_INFLUENCER_ID, DELETED_INFLUENCER_ID):
                    detail = await client.get(f"/api/v1/influencers/{missing_id}")
                    assert detail.status_code == 404
                    assert detail.json()["error"]["code"] == "INFLUENCER_NOT_FOUND"
                    snapshots = await client.get(
                        f"/api/v1/influencers/{missing_id}/metric-snapshots"
                    )
                    assert snapshots.status_code == 404
                    assert snapshots.json()["error"]["code"] == "INFLUENCER_NOT_FOUND"

                invalid_uuid = await client.get("/api/v1/influencers/not-a-uuid")
                assert_validation_error(invalid_uuid)
                snapshot_base = f"/api/v1/influencers/{INFLUENCER_ID}/metric-snapshots"
                default_page = await client.get(snapshot_base)
                assert default_page.json()["data"]["page"] == 1
                assert default_page.json()["data"]["page_size"] == 50
                valid_page = await client.get(snapshot_base, params={"page": "1"})
                assert valid_page.status_code == 200
                valid_page_size = await client.get(snapshot_base, params={"page_size": "100"})
                assert valid_page_size.status_code == 200
                assert_validation_error(await client.get(snapshot_base, params={"page": "1.0"}))
                assert_validation_error(await client.get(snapshot_base, params={"page": "0"}))
                assert_validation_error(
                    await client.get(snapshot_base, params={"page_size": "50.0"})
                )
                assert_validation_error(
                    await client.get(snapshot_base, params={"page_size": "101"})
                )
                assert_validation_error(
                    await client.get(snapshot_base, params=[("page", "1"), ("page", "2")])
                )
                assert_validation_error(
                    await client.get(
                        snapshot_base,
                        params={"unexpected_filter": "ignored-no-longer"},
                    )
                )
                assert_validation_error(
                    await client.get(
                        snapshot_base,
                        params=[("page_size", "50"), ("page_size", "100")],
                    )
                )

    asyncio.run(scenario())


def test_permission_error_translation_uses_existing_error_envelope() -> None:
    class PermissionDeniedService:
        async def list_influencers(self, *_args: object) -> object:
            raise InfluencerPermissionError

    async def scenario() -> None:
        async with api_environment() as environment:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                authenticated_client(client, environment.tokens[Role.OPERATOR])
                app.dependency_overrides[get_influencer_service] = PermissionDeniedService
                denied = await client.get("/api/v1/influencers")
                assert denied.status_code == 403
                assert denied.json()["error"]["code"] == "PERMISSION_DENIED"

    asyncio.run(scenario())


def test_openapi_exposes_only_the_four_frozen_influencer_gets() -> None:
    schema = app.openapi()

    def resolve(contract: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in contract:
            reference = contract["$ref"]
            assert isinstance(reference, str)
            contract = schema["components"]["schemas"][reference.rsplit("/", 1)[-1]]
        return contract

    def success_data(path: str) -> dict[str, Any]:
        operation = schema["paths"][path]["get"]
        response_contract = operation["responses"]["200"]["content"]["application/json"]["schema"]
        envelope_contract = resolve(response_contract)
        assert {"success", "data", "error", "request_id"} <= set(envelope_contract["properties"])
        return resolve(envelope_contract["properties"]["data"])

    influencer_paths = {
        path: item
        for path, item in schema["paths"].items()
        if path.startswith("/api/v1/influencers")
    }
    assert set(influencer_paths) == {
        "/api/v1/influencers",
        "/api/v1/influencers/filter-options",
        "/api/v1/influencers/{influencer_id}",
        "/api/v1/influencers/{influencer_id}/metric-snapshots",
    }
    assert all(
        set(item).isdisjoint({"post", "put", "patch", "delete"})
        for item in influencer_paths.values()
    )
    assert all("get" in item for item in influencer_paths.values())

    list_parameters = {
        parameter["name"]
        for parameter in influencer_paths["/api/v1/influencers"]["get"]["parameters"]
        if parameter["in"] == "query"
    }
    snapshot_parameters = {
        parameter["name"]
        for parameter in influencer_paths["/api/v1/influencers/{influencer_id}/metric-snapshots"][
            "get"
        ]["parameters"]
        if parameter["in"] == "query"
    }
    assert list_parameters == {
        "q",
        "tag",
        "followers_min",
        "followers_max",
        "owner_operator_id",
        "crm_stage",
        "contact_filter",
        "notes_7d_filter",
        "notes_60d_filter",
        "content_activity_filter",
        "freshness_status",
        "requires_refresh",
        "last_huitun_observed_before",
        "last_huitun_observed_after",
        "page",
        "page_size",
    }
    assert snapshot_parameters == {"page", "page_size"}

    parameter_schemas = {
        parameter["name"]: parameter["schema"]
        for parameter in influencer_paths["/api/v1/influencers"]["get"]["parameters"]
        if parameter["in"] == "query"
    }
    assert parameter_schemas["freshness_status"]["anyOf"][0] == {
        "$ref": "#/components/schemas/FreshnessStatus"
    }
    assert schema["components"]["schemas"]["FreshnessStatus"]["enum"] == [
        status.value for status in FreshnessStatus
    ]
    assert parameter_schemas["content_activity_filter"]["anyOf"][0] == {
        "$ref": "#/components/schemas/ContentActivityFilter"
    }
    assert schema["components"]["schemas"]["ContentActivityFilter"]["enum"] == [
        activity_filter.value for activity_filter in ContentActivityFilter
    ]
    assert parameter_schemas["requires_refresh"]["anyOf"][0]["type"] == "boolean"
    assert parameter_schemas["last_huitun_observed_before"]["anyOf"][0] == {
        "type": "string",
        "format": "date-time",
    }
    assert parameter_schemas["last_huitun_observed_after"]["anyOf"][0] == {
        "type": "string",
        "format": "date-time",
    }

    list_page = success_data("/api/v1/influencers")
    list_item = resolve(resolve(list_page["properties"]["items"])["items"])
    assert {"freshness_status", "requires_refresh"} <= set(list_item["properties"])
    account_item = resolve(resolve(list_item["properties"]["platform_accounts"])["items"])
    assert {
        "last_huitun_observed_at",
        "last_huitun_imported_at",
        "freshness_status",
        "freshness_age_days",
        "requires_refresh",
        "content_activity_state",
        "content_activity_trusted_observed_at",
        "content_activity_trusted_observation_status",
        "content_activity_trusted_coverage_status",
        "content_activity_trusted_result",
        "content_activity_last_publication_at",
        "content_activity_inactive_days",
        "content_activity_latest_attempt_observed_at",
        "content_activity_latest_attempt_observation_status",
        "content_activity_latest_attempt_coverage_status",
        "content_activity_latest_attempt_result",
    } <= set(account_item["properties"])

    detail = success_data("/api/v1/influencers/{influencer_id}")
    assert {"freshness_status", "requires_refresh"} <= set(detail["properties"])
    detail_account = resolve(resolve(detail["properties"]["platform_accounts"])["items"])
    assert {
        "last_huitun_observed_at",
        "last_huitun_imported_at",
        "freshness_status",
        "freshness_age_days",
        "requires_refresh",
        "content_activity_state",
        "content_activity_trusted_observed_at",
        "content_activity_trusted_observation_status",
        "content_activity_trusted_coverage_status",
        "content_activity_trusted_result",
        "content_activity_last_publication_at",
        "content_activity_inactive_days",
        "content_activity_latest_attempt_observed_at",
        "content_activity_latest_attempt_observation_status",
        "content_activity_latest_attempt_coverage_status",
        "content_activity_latest_attempt_result",
    } <= set(detail_account["properties"])

    filter_options = success_data("/api/v1/influencers/filter-options")
    assert {"owners", "tags", "crm_stages"} == set(filter_options["properties"])
    snapshot_page = success_data("/api/v1/influencers/{influencer_id}/metric-snapshots")
    assert {"items", "page", "page_size", "total"} == set(snapshot_page["properties"])
