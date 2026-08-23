"""HTTP and broker-boundary contracts for XHS Content Activity commands."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

from app.http.content_activity import get_content_activity_service
from app.http.content_activity_tasks import ContentActivityTaskDispatcher
from app.http.dependencies import (
    get_auth_service,
    get_content_activity_task_dispatcher,
    require_auth,
)
from app.main import app
from backend_core.auth.enums import Role
from backend_core.config import get_settings
from backend_core.content_activity.schemas import (
    ContentActivityRefreshRequestPublic,
    XhsIdentityResolutionPublic,
)
from backend_core.content_activity.service import ContentActivityError
from httpx import ASGITransport, AsyncClient, Response

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000801")
SECOND_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000802")
DEPARTMENT_ID = UUID("00000000-0000-0000-0000-000000000803")
OPERATOR_ID = UUID("00000000-0000-0000-0000-000000000804")
IDENTITY_ID = UUID("00000000-0000-0000-0000-000000000805")
VERIFICATION_ID = UUID("00000000-0000-0000-0000-000000000806")
REQUEST_TOKEN = UUID("00000000-0000-0000-0000-000000000807")
SECOND_REQUEST_TOKEN = UUID("00000000-0000-0000-0000-000000000808")
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _context(role: Role = Role.SUPER_ADMIN) -> object:
    """A minimal dependency value; routes only need stable scoped IDs and role."""

    return SimpleNamespace(
        department=SimpleNamespace(id=DEPARTMENT_ID),
        operator=SimpleNamespace(id=OPERATOR_ID),
        role=role,
    )


class FakeAuthService:
    def validate_csrf(self, _context: object, csrf_token: str | None) -> None:
        assert csrf_token == "content-activity-csrf"


class FakeContentActivityService:
    def __init__(self) -> None:
        self.identity_calls: list[dict[str, object]] = []
        self.refresh_calls: list[dict[str, object]] = []

    async def resolve_xhs_identity(self, **kwargs: object) -> XhsIdentityResolutionPublic:
        self.identity_calls.append(kwargs)
        return XhsIdentityResolutionPublic(
            platform_account_id=ACCOUNT_ID,
            identity_binding_id=IDENTITY_ID,
            verification_event_id=VERIFICATION_ID,
            outcome="VERIFIED_CURRENT",
        )

    async def create_xhs_refresh_requests(
        self, **kwargs: object
    ) -> tuple[ContentActivityRefreshRequestPublic, ...]:
        self.refresh_calls.append(kwargs)
        return (
            ContentActivityRefreshRequestPublic(
                request_token=REQUEST_TOKEN,
                platform_account_id=ACCOUNT_ID,
                state="PENDING",
                attempt_count=0,
                max_attempts=3,
                requested_at=NOW,
            ),
            ContentActivityRefreshRequestPublic(
                request_token=SECOND_REQUEST_TOKEN,
                platform_account_id=SECOND_ACCOUNT_ID,
                state="PENDING",
                attempt_count=0,
                max_attempts=3,
                requested_at=NOW,
            ),
        )


class BatchRejectingContentActivityService(FakeContentActivityService):
    async def create_xhs_refresh_requests(
        self, **kwargs: object
    ) -> tuple[ContentActivityRefreshRequestPublic, ...]:
        self.refresh_calls.append(kwargs)
        raise ContentActivityError(
            422,
            "REFRESH_BATCH_INVALID",
            "Refresh batch size is invalid",
        )


class FakeDispatcher:
    def __init__(self) -> None:
        self.tokens: list[UUID] = []

    async def refresh(self, request_token: UUID) -> None:
        self.tokens.append(request_token)


def _install_overrides(
    *,
    context: object,
    service: FakeContentActivityService,
    dispatcher: FakeDispatcher,
) -> None:
    app.dependency_overrides[require_auth] = lambda: context
    app.dependency_overrides[get_auth_service] = FakeAuthService
    app.dependency_overrides[get_content_activity_service] = lambda: service
    app.dependency_overrides[get_content_activity_task_dispatcher] = lambda: dispatcher


def _assert_error(response: Response, *, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == code


def test_content_activity_openapi_declares_admin_csrf_and_idempotency_headers() -> None:
    document = app.openapi()

    for path in (
        "/api/v1/admin/content-activity/xiaohongshu/identities/resolve",
        "/api/v1/admin/content-activity/xiaohongshu/refreshes",
    ):
        operation = document["paths"][path]["post"]
        headers = {
            parameter["name"]: parameter
            for parameter in operation["parameters"]
            if parameter["in"] == "header"
        }
        assert headers["X-CSRF-Token"]["required"] is True
        assert headers["Idempotency-Key"]["required"] is True

    identity_response = document["components"]["schemas"]["XhsIdentityResolutionPublic"]
    assert "bootstrap_input" not in identity_response["properties"]
    assert set(identity_response["required"]) == {
        "platform_account_id",
        "identity_binding_id",
        "verification_event_id",
        "outcome",
    }


def test_identity_endpoint_enforces_admin_guards_and_never_echoes_bootstrap() -> None:
    async def scenario() -> None:
        service = FakeContentActivityService()
        dispatcher = FakeDispatcher()
        _install_overrides(context=_context(), service=service, dispatcher=dispatcher)
        sentinel = "RAW_XHS_BOOTSTRAP_MUST_NOT_BE_ECHOED"
        payload = {"platform_account_id": str(ACCOUNT_ID), "bootstrap_input": sentinel}
        settings = get_settings()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                missing_csrf = await client.post(
                    "/api/v1/admin/content-activity/xiaohongshu/identities/resolve",
                    json=payload,
                    headers={"Idempotency-Key": "identity-key"},
                )
                _assert_error(missing_csrf, status_code=403, code="CSRF_FAILED")
                assert service.identity_calls == []

                client.cookies.set(settings.csrf_cookie_name, "content-activity-csrf")
                missing_idempotency = await client.post(
                    "/api/v1/admin/content-activity/xiaohongshu/identities/resolve",
                    json=payload,
                    headers={"X-CSRF-Token": "content-activity-csrf"},
                )
                _assert_error(
                    missing_idempotency,
                    status_code=422,
                    code="IDEMPOTENCY_KEY_INVALID",
                )
                assert service.identity_calls == []

                duplicate_idempotency = await client.post(
                    "/api/v1/admin/content-activity/xiaohongshu/identities/resolve",
                    json=payload,
                    headers=[
                        ("X-CSRF-Token", "content-activity-csrf"),
                        ("Idempotency-Key", "identity-key-a"),
                        ("Idempotency-Key", "identity-key-b"),
                    ],
                )
                _assert_error(
                    duplicate_idempotency,
                    status_code=422,
                    code="IDEMPOTENCY_KEY_INVALID",
                )
                assert service.identity_calls == []

                accepted = await client.post(
                    "/api/v1/admin/content-activity/xiaohongshu/identities/resolve",
                    json=payload,
                    headers={
                        "X-CSRF-Token": "content-activity-csrf",
                        "Idempotency-Key": "identity-key",
                        "User-Agent": "content-activity-http-test",
                    },
                )
                assert accepted.status_code == 201
                assert accepted.json()["data"] == {
                    "platform_account_id": str(ACCOUNT_ID),
                    "identity_binding_id": str(IDENTITY_ID),
                    "verification_event_id": str(VERIFICATION_ID),
                    "outcome": "VERIFIED_CURRENT",
                }
                serialized = json.dumps(accepted.json(), sort_keys=True)
                assert sentinel not in serialized
                assert "bootstrap_input" not in serialized
                assert service.identity_calls == [
                    {
                        "platform_account_id": ACCOUNT_ID,
                        "bootstrap_input": sentinel,
                        "idempotency_key": "identity-key",
                        "operator_id": OPERATOR_ID,
                        "department_id": DEPARTMENT_ID,
                        "ip": "127.0.0.1",
                        "user_agent": "content-activity-http-test",
                    }
                ]
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_content_activity_commands_reject_non_admin_before_service_work() -> None:
    async def scenario() -> None:
        service = FakeContentActivityService()
        dispatcher = FakeDispatcher()
        _install_overrides(context=_context(Role.OPERATOR), service=service, dispatcher=dispatcher)
        settings = get_settings()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                client.cookies.set(settings.csrf_cookie_name, "content-activity-csrf")
                rejected = await client.post(
                    "/api/v1/admin/content-activity/xiaohongshu/identities/resolve",
                    json={
                        "platform_account_id": str(ACCOUNT_ID),
                        "bootstrap_input": "not-used",
                    },
                    headers={
                        "X-CSRF-Token": "content-activity-csrf",
                        "Idempotency-Key": "identity-key",
                    },
                )
                _assert_error(rejected, status_code=403, code="PERMISSION_DENIED")
                assert service.identity_calls == []
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_refresh_endpoint_enforces_csrf_admin_scope_and_batch_rejection() -> None:
    async def scenario() -> None:
        service = BatchRejectingContentActivityService()
        dispatcher = FakeDispatcher()
        _install_overrides(context=_context(), service=service, dispatcher=dispatcher)
        settings = get_settings()
        payload = {"platform_account_ids": [str(ACCOUNT_ID), str(SECOND_ACCOUNT_ID)]}
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                missing_csrf = await client.post(
                    "/api/v1/admin/content-activity/xiaohongshu/refreshes",
                    json=payload,
                    headers={"Idempotency-Key": "refresh-key"},
                )
                _assert_error(missing_csrf, status_code=403, code="CSRF_FAILED")
                assert service.refresh_calls == []

                client.cookies.set(settings.csrf_cookie_name, "content-activity-csrf")
                app.dependency_overrides[require_auth] = lambda: _context(Role.OPERATOR)
                non_admin = await client.post(
                    "/api/v1/admin/content-activity/xiaohongshu/refreshes",
                    json=payload,
                    headers={
                        "X-CSRF-Token": "content-activity-csrf",
                        "Idempotency-Key": "refresh-key",
                    },
                )
                _assert_error(non_admin, status_code=403, code="PERMISSION_DENIED")
                assert service.refresh_calls == []

                app.dependency_overrides[require_auth] = lambda: _context()
                batch_rejected = await client.post(
                    "/api/v1/admin/content-activity/xiaohongshu/refreshes",
                    json=payload,
                    headers={
                        "X-CSRF-Token": "content-activity-csrf",
                        "Idempotency-Key": "refresh-key",
                        "User-Agent": "content-activity-http-test",
                    },
                )
                _assert_error(batch_rejected, status_code=422, code="REFRESH_BATCH_INVALID")
                assert service.refresh_calls == [
                    {
                        "platform_account_ids": (ACCOUNT_ID, SECOND_ACCOUNT_ID),
                        "idempotency_key": "refresh-key",
                        "operator_id": OPERATOR_ID,
                        "department_id": DEPARTMENT_ID,
                        "ip": "127.0.0.1",
                        "user_agent": "content-activity-http-test",
                    }
                ]
                assert dispatcher.tokens == []
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_refresh_endpoint_dispatches_only_durable_request_uuids() -> None:
    async def scenario() -> None:
        service = FakeContentActivityService()
        dispatcher = FakeDispatcher()
        _install_overrides(context=_context(), service=service, dispatcher=dispatcher)
        settings = get_settings()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                client.cookies.set(settings.csrf_cookie_name, "content-activity-csrf")
                accepted = await client.post(
                    "/api/v1/admin/content-activity/xiaohongshu/refreshes",
                    json={"platform_account_ids": [str(ACCOUNT_ID), str(SECOND_ACCOUNT_ID)]},
                    headers={
                        "X-CSRF-Token": "content-activity-csrf",
                        "Idempotency-Key": "refresh-key",
                        "User-Agent": "content-activity-http-test",
                    },
                )
                assert accepted.status_code == 201
                assert dispatcher.tokens == [REQUEST_TOKEN, SECOND_REQUEST_TOKEN]
                assert service.refresh_calls == [
                    {
                        "platform_account_ids": (ACCOUNT_ID, SECOND_ACCOUNT_ID),
                        "idempotency_key": "refresh-key",
                        "operator_id": OPERATOR_ID,
                        "department_id": DEPARTMENT_ID,
                        "ip": "127.0.0.1",
                        "user_agent": "content-activity-http-test",
                    }
                ]
                body = accepted.json()
                assert [item["request_token"] for item in body["data"]["requests"]] == [
                    str(REQUEST_TOKEN),
                    str(SECOND_REQUEST_TOKEN),
                ]
                assert "bootstrap_input" not in json.dumps(body, sort_keys=True)
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_content_activity_dispatcher_publishes_exactly_one_uuid_only_message() -> None:
    celery_client = MagicMock()
    dispatcher = ContentActivityTaskDispatcher(celery_client)

    asyncio.run(dispatcher.refresh(REQUEST_TOKEN))

    celery_client.send_task.assert_called_once_with(
        "content_activity.refresh_request",
        args=[str(REQUEST_TOKEN)],
        task_id=str(REQUEST_TOKEN),
        queue="analytics",
        retry=False,
    )
