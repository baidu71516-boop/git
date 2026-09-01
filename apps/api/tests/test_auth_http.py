import asyncio
from collections.abc import AsyncIterator

from app.http.dependencies import get_database_session, get_redis
from app.main import app
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, DepartmentPermission, Operator
from backend_core.auth.security import hash_password
from backend_core.config import get_settings
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
                name="Viewer Operator",
                role=Role.VIEWER,
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
                        # Public `role` remains the legacy Department-role field until Web and
                        # API response composition change together in a later Task.
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


def test_operator_directory_uses_phase3a_department_scope_without_selected_operator() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        fake_redis = FakeRedis(decode_responses=True)

        async with factory() as session:
            super_department = Department(
                name="Operator Scope Super",
                password_hash=hash_password("super-password"),
                status=DepartmentStatus.ACTIVE,
                session_days=30,
            )
            target_department = Department(
                name="Operator Scope Target",
                password_hash=hash_password("target-password"),
                status=DepartmentStatus.ACTIVE,
                session_days=30,
            )
            normal_department = Department(
                name="Operator Scope Normal",
                password_hash=hash_password("normal-password"),
                status=DepartmentStatus.ACTIVE,
                session_days=30,
            )
            disabled_department = Department(
                name="Operator Scope Disabled",
                password_hash=hash_password("disabled-password"),
                status=DepartmentStatus.DISABLED,
                session_days=30,
            )
            session.add_all(
                (super_department, target_department, normal_department, disabled_department)
            )
            await session.flush()
            super_operator = Operator(
                department_id=super_department.id,
                name="Super Operator",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
            )
            target_active = Operator(
                department_id=target_department.id,
                name="Target Active",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
            )
            target_disabled = Operator(
                department_id=target_department.id,
                name="Target Disabled",
                role=Role.OPERATOR,
                status=OperatorStatus.DISABLED,
            )
            normal_operator = Operator(
                department_id=normal_department.id,
                name="Normal Operator",
                role=Role.SUPER_ADMIN,
                status=OperatorStatus.ACTIVE,
            )
            session.add_all(
                (
                    DepartmentPermission(department_id=super_department.id, role=Role.SUPER_ADMIN),
                    DepartmentPermission(department_id=target_department.id, role=Role.OPERATOR),
                    DepartmentPermission(department_id=normal_department.id, role=Role.VIEWER),
                    super_operator,
                    target_active,
                    target_disabled,
                    normal_operator,
                )
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
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        super_login = await client.post(
                            "/api/v1/auth/login",
                            json={
                                "department_id": str(super_department.id),
                                "password": "super-password",
                            },
                        )
                        assert super_login.status_code == 200

                        own = await client.get("/api/v1/operators")
                        assert own.status_code == 200
                        assert [item["id"] for item in own.json()["data"]] == [
                            str(super_operator.id)
                        ]

                        cross_department = await client.get(
                            "/api/v1/operators",
                            headers={"X-Department-ID": str(target_department.id)},
                        )
                        assert cross_department.status_code == 200
                        assert cross_department.json()["data"] == [
                            {
                                "id": str(target_active.id),
                                "department_id": str(target_department.id),
                                "name": target_active.name,
                                "role": "operator",
                                "status": "active",
                            }
                        ]

                        disabled_department_response = await client.get(
                            "/api/v1/operators",
                            headers={"X-Department-ID": str(disabled_department.id)},
                        )
                        assert disabled_department_response.status_code == 404
                        assert (
                            disabled_department_response.json()["error"]["code"]
                            == "DEPARTMENT_NOT_FOUND"
                        )

                        invalid = await client.get(
                            "/api/v1/operators",
                            headers={"X-Department-ID": "not-a-uuid"},
                        )
                        assert invalid.status_code == 422
                        duplicated = await client.get(
                            "/api/v1/operators",
                            headers=[
                                ("X-Department-ID", str(target_department.id)),
                                ("X-Department-ID", str(target_department.id)),
                            ],
                        )
                        assert duplicated.status_code == 422

                        normal_login = await client.post(
                            "/api/v1/auth/login",
                            json={
                                "department_id": str(normal_department.id),
                                "password": "normal-password",
                            },
                        )
                        assert normal_login.status_code == 200
                        # No selected Operator: Viewer reads are still allowed.
                        normal_own = await client.get("/api/v1/operators")
                        assert normal_own.status_code == 200
                        assert [item["id"] for item in normal_own.json()["data"]] == [
                            str(normal_operator.id)
                        ]
                        concealed = await client.get(
                            "/api/v1/operators",
                            headers={"X-Department-ID": str(target_department.id)},
                        )
                        assert concealed.status_code == 404
                        assert concealed.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

                    document = app.openapi()
                    operation = document["paths"]["/api/v1/operators"]["get"]
                    header = next(
                        parameter
                        for parameter in operation["parameters"]
                        if parameter["in"] == "header" and parameter["name"] == "X-Department-ID"
                    )
                    assert header["required"] is False
                    assert "OperatorPublic" in str(
                        operation["responses"]["200"]["content"]["application/json"]["schema"]
                    )
            finally:
                app.dependency_overrides.clear()

        await fake_redis.aclose()
        await engine.dispose()

    asyncio.run(scenario())
