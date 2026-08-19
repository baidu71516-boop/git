import asyncio
from collections.abc import AsyncIterator

from app.http.dependencies import get_database_session, get_redis
from app.main import app
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, DepartmentPermission, Operator
from backend_core.auth.security import hash_password
from backend_core.config import get_settings
from backend_core.db import models as database_models  # noqa: F401
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.phase3a_auth_database import database_session


def test_auth_http_cookie_csrf_and_operator_permission_boundaries() -> None:
    async def scenario() -> None:
        settings = get_settings()
        fake_redis = FakeRedis(decode_responses=True)

        async with database_session() as session:
            department = Department(
                name="HTTP Test",
                password_hash=hash_password("http-test-password"),
                status=DepartmentStatus.ACTIVE,
                session_days=30,
            )
            session.add(department)
            await session.flush()
            operator = Operator(
                department_id=department.id,
                name="Privileged Name Only",
                role=Role.SUPER_ADMIN,
                status=OperatorStatus.ACTIVE,
            )
            session.add_all(
                [
                    DepartmentPermission(department_id=department.id, role=Role.VIEWER),
                    operator,
                ]
            )
            await session.commit()

            async def override_database_session() -> AsyncIterator[AsyncSession]:
                yield session

            def override_redis() -> FakeRedis:
                return fake_redis

            app.dependency_overrides[get_database_session] = override_database_session
            app.dependency_overrides[get_redis] = override_redis
            try:
                async with app.router.lifespan_context(app):
                    transport = ASGITransport(app=app)
                    async with AsyncClient(
                        transport=transport,
                        base_url="http://test",
                    ) as client:
                        unauthenticated = await client.get("/api/v1/operators")
                        assert unauthenticated.status_code == 401
                        assert unauthenticated.json()["error"]["code"] == "AUTH_REQUIRED"

                        rejected_password = "sensitive-" + ("x" * 300)
                        validation_error = await client.post(
                            "/api/v1/auth/login",
                            json={
                                "department_id": str(department.id),
                                "password": rejected_password,
                                "remember_me": False,
                            },
                        )
                        assert validation_error.status_code == 422
                        assert rejected_password not in validation_error.text

                        login = await client.post(
                            "/api/v1/auth/login",
                            json={
                                "department_id": str(department.id),
                                "password": "http-test-password",
                                "remember_me": False,
                            },
                        )
                        assert login.status_code == 200
                        assert login.json()["data"]["role"] == "viewer"
                        assert "session_token" not in login.text
                        session_cookie = login.headers.get_list("set-cookie")[0].lower()
                        assert "httponly" in session_cookie
                        assert "samesite=lax" in session_cookie
                        assert client.cookies.get(settings.session_cookie_name) is not None
                        csrf_token = client.cookies.get(settings.csrf_cookie_name)
                        assert csrf_token is not None

                        before_selection = await client.get("/api/v1/auth/me")
                        assert before_selection.status_code == 200
                        assert before_selection.json()["data"]["operator"] is None

                        csrf_rejected = await client.post(
                            "/api/v1/auth/select-operator",
                            json={"operator_id": str(operator.id)},
                        )
                        assert csrf_rejected.status_code == 403
                        assert csrf_rejected.json()["error"]["code"] == "CSRF_FAILED"

                        selected = await client.post(
                            "/api/v1/auth/select-operator",
                            json={"operator_id": str(operator.id)},
                            headers={"X-CSRF-Token": csrf_token},
                        )
                        assert selected.status_code == 200
                        assert selected.json()["data"]["operator"]["id"] == str(operator.id)
                        # Operator Role is attribution data; authority remains Department Role.
                        assert selected.json()["data"]["role"] == "viewer"

                        denied_admin = await client.post(
                            f"/api/v1/admin/departments/{department.id}/reset-password",
                            json={"new_password": "replacement-password"},
                            headers={"X-CSRF-Token": csrf_token},
                        )
                        assert denied_admin.status_code == 403
                        assert denied_admin.json()["error"]["code"] == "PERMISSION_DENIED"

                        logout = await client.post(
                            "/api/v1/auth/logout",
                            headers={"X-CSRF-Token": csrf_token},
                        )
                        assert logout.status_code == 200
                        after_logout = await client.get("/api/v1/auth/me")
                        assert after_logout.status_code == 401
            finally:
                app.dependency_overrides.clear()

        await fake_redis.aclose()

    asyncio.run(scenario())
