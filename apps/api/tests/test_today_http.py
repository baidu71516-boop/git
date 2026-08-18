"""HTTP/OpenAPI regressions for the sealed Phase 3A Today contract."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import uuid4

from app.http.dependencies import get_database_session, require_auth
from app.http.today import get_today_service
from app.main import app
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, Operator
from backend_core.auth.service import AuthContext
from backend_core.outreach.today import TodayPage
from httpx import ASGITransport, AsyncClient, Response


def _context() -> AuthContext:
    department = Department(
        id=uuid4(),
        name=f"Today HTTP {uuid4().hex}",
        password_hash="not-used",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    operator = Operator(
        id=uuid4(),
        department_id=department.id,
        name="Today HTTP Operator",
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    return AuthContext(
        department=department,
        operator=operator,
        role=Role.OPERATOR,
        auth_session=AuthSession(
            id=uuid4(),
            department_id=department.id,
            operator_id=operator.id,
            token_hash="a" * 64,
            csrf_token_hash="b" * 64,
            ip="192.0.2.44",
            user_agent="today-http-test",
            expires_at=datetime(2026, 9, 1, tzinfo=UTC),
            revoked_at=None,
        ),
    )


class FakeTodayService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, object]] = []

    async def list_today(
        self,
        context: AuthContext,
        query: object,
        *,
        department_id: object,
    ) -> TodayPage:
        self.calls.append((context, query, department_id))
        return TodayPage(
            business_date=date(2026, 8, 21),
            as_of=datetime(2026, 8, 21, 7, tzinfo=UTC),
            items=(),
            next_cursor=None,
        )


async def _fake_database_session() -> AsyncIterator[object]:
    yield object()


def _install_overrides(context: AuthContext, service: FakeTodayService) -> None:
    app.dependency_overrides[require_auth] = lambda: context
    app.dependency_overrides[get_database_session] = _fake_database_session
    app.dependency_overrides[get_today_service] = lambda: service


def _assert_validation_error(response: Response) -> None:
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_today_route_precedence_closed_query_and_scope_header() -> None:
    async def scenario() -> None:
        context = _context()
        service = FakeTodayService()
        _install_overrides(context, service)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resolved = await client.get(
                    "/api/v1/outreach-tasks/today",
                    params={"owner_operator_id": str(context.operator.id)},
                )
                assert resolved.status_code == 200
                assert resolved.json()["data"]["business_date"] == "2026-08-21"
                assert len(service.calls) == 1

                repeated_query = await client.get("/api/v1/outreach-tasks/today?limit=1&limit=2")
                _assert_validation_error(repeated_query)

                unknown_query = await client.get("/api/v1/outreach-tasks/today?department_id=x")
                _assert_validation_error(unknown_query)

                repeated_header = await client.get(
                    "/api/v1/outreach-tasks/today",
                    headers=[
                        ("X-Department-ID", str(context.department.id)),
                        ("X-Department-ID", str(context.department.id)),
                    ],
                )
                _assert_validation_error(repeated_header)
                # The parser/scope guard rejects each malformed request before
                # any projection service call.
                assert len(service.calls) == 1
        finally:
            app.dependency_overrides.clear()

    asyncio.run(scenario())


def test_today_openapi_documents_frozen_owner_track_and_business_date_meaning() -> None:
    schema = app.openapi()
    operation = schema["paths"]["/api/v1/outreach-tasks/today"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert set(parameters) == {
        "work_kind",
        "channel",
        "campaign_id",
        "owner_operator_id",
        "track",
        "followers_min",
        "followers_max",
        "contact_filter",
        "priority",
        "cursor",
        "limit",
        "X-Department-ID",
    }
    assert "assigned_operator_id" in parameters["owner_operator_id"]["description"]
    assert "exact" in parameters["track"]["description"].lower()
    assert "Super Admin" in parameters["X-Department-ID"]["description"]
    today_page = schema["components"]["schemas"]["TodayPage"]
    assert (
        "local calendar date of as_of" in today_page["properties"]["business_date"]["description"]
    )
