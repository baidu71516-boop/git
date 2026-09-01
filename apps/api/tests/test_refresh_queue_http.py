"""HTTP contract and real auth/RBAC coverage for Phase 2 Refresh Queues."""

import asyncio
import csv
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from uuid import UUID, uuid4

from app.http.dependencies import get_database_session, get_redis
from app.main import app
from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
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
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.influencers.enums import CRMStage, DataSource, InfluencerStatus, Platform
from backend_core.influencers.models import (
    Influencer,
    InfluencerPlatformAccount,
    InfluencerSourceState,
)
from backend_core.refresh.csv_export import REFRESH_QUEUE_CSV_COLUMNS
from backend_core.refresh.enums import RefreshQueueItemStatus
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

NOW = datetime(2026, 8, 13, 4, 0, tzinfo=UTC)
FORMULA_ACCOUNT_NAME = "  =SUM(1,1)"


@dataclass(frozen=True)
class Credential:
    department_id: UUID
    token: str
    csrf: str


@dataclass(frozen=True)
class ApiEnvironment:
    session: AsyncSession
    credentials: dict[str, Credential]


async def _seed_credential(
    session: AsyncSession,
    *,
    name: str,
    role: Role,
    operator_selected: bool = True,
) -> Credential:
    department = Department(
        name=f"Refresh HTTP {name}",
        password_hash="not-used-by-http-test",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name=f"Refresh {name} operator",
        role=role,
        status=OperatorStatus.ACTIVE,
        password_hash=(
            hash_password(f"refresh-{name}-operator-test-password") if operator_selected else None
        ),
        credential_version=1 if operator_selected else 0,
    )
    session.add(operator)
    await session.flush()
    token = f"refresh-http-session-{name}"
    csrf = f"refresh-http-csrf-{name}"
    session.add_all(
        [
            DepartmentPermission(department_id=department.id, role=role),
            AuthSession(
                department_id=department.id,
                operator_id=operator.id if operator_selected else None,
                operator_credential_version=1 if operator_selected else None,
                token_hash=hash_token(token),
                csrf_token_hash=hash_token(csrf),
                ip="192.0.2.80",
                user_agent="refresh-http-test",
                expires_at=datetime.now(UTC) + timedelta(hours=12),
                revoked_at=None,
            ),
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
    return Credential(department_id=department.id, token=token, csrf=csrf)


async def _seed_company_candidate(session: AsyncSession) -> None:
    influencer = Influencer(
        display_name="Refresh HTTP candidate",
        crm_stage=CRMStage.TO_DEVELOP,
        status=InfluencerStatus.ACTIVE,
        deleted_at=None,
    )
    session.add(influencer)
    await session.flush()
    account = InfluencerPlatformAccount(
        influencer_id=influencer.id,
        platform=Platform.XIAOHONGSHU,
        platform_account_id=None,
        account_name=FORMULA_ACCOUNT_NAME,
        account_handle=None,
        profile_url=None,
        normalized_profile_url=None,
        # Eligibility must come from explicit Huitun evidence, never this field.
        source=DataSource.MANUAL,
        is_active=True,
        source_tags=[],
    )
    session.add(account)
    await session.flush()
    session.add(
        InfluencerSourceState(
            influencer_id=influencer.id,
            platform_account_id=account.id,
            source=DataSource.HUITUN,
            source_updated_at=NOW,
            source_data={"public_fixture": True},
            source_data_hash="a" * 64,
            state_version=1,
            last_import_job_id=uuid4(),
            last_import_row_id=uuid4(),
        )
    )


@asynccontextmanager
async def api_environment() -> AsyncIterator[ApiEnvironment]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = FakeRedis(decode_responses=True)
    async with factory() as session:
        credentials = {
            "operator": await _seed_credential(session, name="operator", role=Role.OPERATOR),
            "viewer": await _seed_credential(session, name="viewer", role=Role.VIEWER),
            "admin": await _seed_credential(session, name="admin", role=Role.SUPER_ADMIN),
            "no_operator": await _seed_credential(
                session,
                name="no-operator",
                role=Role.MANAGER,
                operator_selected=False,
            ),
        }
        await _seed_company_candidate(session)
        await session.commit()

        async def override_database_session() -> AsyncIterator[AsyncSession]:
            yield session

        def override_redis() -> FakeRedis:
            return redis

        app.dependency_overrides[get_database_session] = override_database_session
        app.dependency_overrides[get_redis] = override_redis
        try:
            yield ApiEnvironment(session=session, credentials=credentials)
        finally:
            app.dependency_overrides.clear()
    await redis.aclose()
    await engine.dispose()


def _authenticate(
    client: AsyncClient,
    credential: Credential,
    *,
    csrf_cookie: bool = True,
) -> None:
    client.cookies.clear()
    settings = get_settings()
    client.cookies.set(settings.session_cookie_name, credential.token)
    if csrf_cookie:
        client.cookies.set(settings.csrf_cookie_name, credential.csrf)


def _csrf_headers(credential: Credential) -> dict[str, str]:
    return {"X-CSRF-Token": credential.csrf}


def _create_payload(*, department_id: UUID | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "requested_limit": 1,
        "today_total_limit": 20,
        "refresh_limit": 10,
    }
    if department_id is not None:
        payload["department_id"] = str(department_id)
    return payload


def _assert_error(response: Response, *, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == code
    assert body["request_id"]


def test_refresh_queue_http_auth_scope_audit_csv_and_state_machine() -> None:
    async def scenario() -> None:
        async with api_environment() as environment:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                credentials = environment.credentials
                _assert_error(
                    await client.get("/api/v1/refresh-queues"),
                    status_code=401,
                    code="AUTH_REQUIRED",
                )

                operator = credentials["operator"]
                _authenticate(client, operator, csrf_cookie=False)
                csrf_missing = await client.post("/api/v1/refresh-queues", json=_create_payload())
                _assert_error(csrf_missing, status_code=403, code="CSRF_FAILED")
                client.cookies.set(get_settings().csrf_cookie_name, operator.csrf)
                csrf_wrong = await client.post(
                    "/api/v1/refresh-queues",
                    json=_create_payload(),
                    headers={"X-CSRF-Token": "wrong-token"},
                )
                _assert_error(csrf_wrong, status_code=403, code="CSRF_FAILED")

                created = await client.post(
                    "/api/v1/refresh-queues",
                    json=_create_payload(),
                    headers={**_csrf_headers(operator), "X-Request-ID": "refresh-create"},
                )
                assert created.status_code == 201
                assert created.headers["X-Request-ID"] == "refresh-create"
                created_body = created.json()
                assert created_body["success"] is True
                assert created_body["request_id"] == "refresh-create"
                assert created_body["data"]["summary"] == {
                    "requested": 1,
                    "selected": 1,
                    "unique_influencers": 1,
                    "freshness_breakdown": {"unknown": 1},
                    "priority_breakdown": {"1": 1},
                    "status_breakdown": {"pending": 1},
                }
                operator_queue_id = UUID(created_body["data"]["queue"]["id"])
                assert created_body["data"]["queue"]["department_id"] == str(operator.department_id)

                cross_department = await client.post(
                    "/api/v1/refresh-queues",
                    json=_create_payload(department_id=credentials["viewer"].department_id),
                    headers=_csrf_headers(operator),
                )
                _assert_error(cross_department, status_code=403, code="PERMISSION_DENIED")

                no_operator = credentials["no_operator"]
                _authenticate(client, no_operator)
                _assert_error(
                    await client.post(
                        "/api/v1/refresh-queues",
                        json=_create_payload(),
                        headers=_csrf_headers(no_operator),
                    ),
                    status_code=409,
                    code="OPERATOR_REQUIRED",
                )

                viewer = credentials["viewer"]
                _authenticate(client, viewer)
                _assert_error(
                    await client.post(
                        "/api/v1/refresh-queues",
                        json=_create_payload(),
                        headers=_csrf_headers(viewer),
                    ),
                    status_code=403,
                    code="PERMISSION_DENIED",
                )

                admin = credentials["admin"]
                _authenticate(client, admin)
                targeted = await client.post(
                    "/api/v1/refresh-queues",
                    json=_create_payload(department_id=viewer.department_id),
                    headers=_csrf_headers(admin),
                )
                assert targeted.status_code == 201
                target_data = targeted.json()["data"]
                target_queue_id = UUID(target_data["queue"]["id"])
                assert target_data["queue"]["department_id"] == str(viewer.department_id)
                assert target_data["summary"]["selected"] == 1

                before_get_audits = int(
                    await environment.session.scalar(select(func.count(AuditLog.id))) or 0
                )
                # Read routes require only authentication: no CSRF cookie or header.
                _authenticate(client, viewer, csrf_cookie=False)
                viewer_list = await client.get("/api/v1/refresh-queues")
                assert viewer_list.status_code == 200
                assert viewer_list.json()["data"]["total"] == 1
                assert viewer_list.json()["data"]["items"][0]["id"] == str(target_queue_id)
                viewer_detail = await client.get(f"/api/v1/refresh-queues/{target_queue_id}")
                assert viewer_detail.status_code == 200
                viewer_items = await client.get(f"/api/v1/refresh-queues/{target_queue_id}/items")
                assert viewer_items.status_code == 200
                item = viewer_items.json()["data"]["items"][0]
                assert item["priority_tier"] == 1
                assert item["priority_reasons"] == [
                    "FRESHNESS_UNKNOWN",
                    "FOLLOWERS_MISSING",
                ]
                assert item["identity_snapshot"]["account_name"] == FORMULA_ACCOUNT_NAME
                after_get_audits = int(
                    await environment.session.scalar(select(func.count(AuditLog.id))) or 0
                )
                assert after_get_audits == before_get_audits

                _authenticate(client, operator)
                _assert_error(
                    await client.get(f"/api/v1/refresh-queues/{target_queue_id}"),
                    status_code=404,
                    code="REFRESH_QUEUE_NOT_FOUND",
                )
                own_list = await client.get("/api/v1/refresh-queues")
                assert own_list.json()["data"]["total"] == 1
                assert own_list.json()["data"]["items"][0]["id"] == str(operator_queue_id)

                _authenticate(client, admin)
                admin_list = await client.get("/api/v1/refresh-queues")
                assert admin_list.json()["data"]["total"] == 2
                first_export = await client.post(
                    f"/api/v1/refresh-queues/{target_queue_id}/export",
                    headers=_csrf_headers(admin),
                )
                assert first_export.status_code == 200
                assert first_export.headers["content-type"].startswith("text/csv")
                assert first_export.headers["content-disposition"] == (
                    f'attachment; filename="refresh-queue-{target_queue_id}.csv"'
                )
                parsed = list(csv.DictReader(StringIO(first_export.text)))
                assert tuple(parsed[0]) == REFRESH_QUEUE_CSV_COLUMNS
                assert len(parsed) == 1
                assert parsed[0]["account_name"] == f"'{FORMULA_ACCOUNT_NAME}"
                assert "contact" not in first_export.text.lower()

                repeated_export = await client.post(
                    f"/api/v1/refresh-queues/{target_queue_id}/export",
                    headers=_csrf_headers(admin),
                )
                assert repeated_export.status_code == 200
                assert repeated_export.text == first_export.text

                cancelled = await client.post(
                    f"/api/v1/refresh-queues/{target_queue_id}/cancel",
                    headers=_csrf_headers(admin),
                )
                assert cancelled.status_code == 200
                assert cancelled.json()["data"]["queue"]["status"] == "cancelled"
                assert cancelled.json()["data"]["summary"]["status_breakdown"] == {"cancelled": 1}
                cancelled_items = await client.get(
                    f"/api/v1/refresh-queues/{target_queue_id}/items"
                )
                assert cancelled_items.json()["data"]["items"][0]["status"] == (
                    RefreshQueueItemStatus.CANCELLED.value
                )
                _assert_error(
                    await client.post(
                        f"/api/v1/refresh-queues/{target_queue_id}/export",
                        headers=_csrf_headers(admin),
                    ),
                    status_code=409,
                    code="REFRESH_QUEUE_NOT_EXPORTABLE",
                )
                _assert_error(
                    await client.post(
                        f"/api/v1/refresh-queues/{target_queue_id}/cancel",
                        headers=_csrf_headers(admin),
                    ),
                    status_code=409,
                    code="REFRESH_QUEUE_NOT_CANCELLABLE",
                )

                actions = list(
                    await environment.session.scalars(
                        select(AuditLog.action).where(
                            AuditLog.action.in_(
                                (
                                    AuditAction.REFRESH_QUEUE_CREATED,
                                    AuditAction.REFRESH_QUEUE_EXPORTED,
                                    AuditAction.REFRESH_QUEUE_CANCELLED,
                                )
                            )
                        )
                    )
                )
                assert actions.count(AuditAction.REFRESH_QUEUE_CREATED) == 2
                assert actions.count(AuditAction.REFRESH_QUEUE_EXPORTED) == 2
                assert actions.count(AuditAction.REFRESH_QUEUE_CANCELLED) == 1

    asyncio.run(scenario())


def test_refresh_queue_input_pagination_and_openapi_contract() -> None:
    async def scenario() -> None:
        async with api_environment() as environment:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                operator = environment.credentials["operator"]
                _authenticate(client, operator)
                invalid_payloads: list[dict[str, object]] = [
                    {**_create_payload(), "as_of": NOW.isoformat()},
                    {**_create_payload(), "requested_limit": True},
                    {**_create_payload(), "requested_limit": 0},
                    {**_create_payload(), "requested_limit": 2_001},
                    {**_create_payload(), "requested_limit": 11},
                    {**_create_payload(), "refresh_limit": 21},
                ]
                for payload in invalid_payloads:
                    response = await client.post(
                        "/api/v1/refresh-queues",
                        json=payload,
                        headers=_csrf_headers(operator),
                    )
                    _assert_error(response, status_code=422, code="VALIDATION_ERROR")

                for params in (
                    {"offset": "-1"},
                    {"offset": "1.0"},
                    {"limit": "0"},
                    {"limit": "201"},
                    {"limit": "50.0"},
                    {"unknown": "1"},
                ):
                    _assert_error(
                        await client.get("/api/v1/refresh-queues", params=params),
                        status_code=422,
                        code="VALIDATION_ERROR",
                    )
                _assert_error(
                    await client.get(
                        "/api/v1/refresh-queues",
                        params=[("limit", "50"), ("limit", "100")],
                    ),
                    status_code=422,
                    code="VALIDATION_ERROR",
                )
                default_page = await client.get("/api/v1/refresh-queues")
                assert default_page.status_code == 200
                assert default_page.json()["data"]["offset"] == 0
                assert default_page.json()["data"]["limit"] == 50
                max_page = await client.get("/api/v1/refresh-queues", params={"limit": "200"})
                assert max_page.status_code == 200
                assert max_page.json()["data"]["limit"] == 200

    asyncio.run(scenario())

    schema = app.openapi()
    refresh_paths = {
        path: operations
        for path, operations in schema["paths"].items()
        if path.startswith("/api/v1/refresh-queues")
    }
    assert set(refresh_paths) == {
        "/api/v1/refresh-queues",
        "/api/v1/refresh-queues/{refresh_queue_id}",
        "/api/v1/refresh-queues/{refresh_queue_id}/items",
        "/api/v1/refresh-queues/{refresh_queue_id}/export",
        "/api/v1/refresh-queues/{refresh_queue_id}/cancel",
    }
    collection_methods = {
        method for method in refresh_paths["/api/v1/refresh-queues"] if method != "parameters"
    }
    assert collection_methods == {
        "get",
        "post",
    }
    assert "get" in refresh_paths["/api/v1/refresh-queues/{refresh_queue_id}"]
    assert "get" in refresh_paths["/api/v1/refresh-queues/{refresh_queue_id}/items"]
    assert "post" in refresh_paths["/api/v1/refresh-queues/{refresh_queue_id}/export"]
    assert "post" in refresh_paths["/api/v1/refresh-queues/{refresh_queue_id}/cancel"]
    export_response = refresh_paths["/api/v1/refresh-queues/{refresh_queue_id}/export"]["post"][
        "responses"
    ]["200"]
    assert "text/csv" in export_response["content"]
