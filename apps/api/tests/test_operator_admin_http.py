"""Focused HTTP contracts for Permissions V1 Operator administration."""

import asyncio
from collections.abc import AsyncIterator

from app.http.dependencies import get_database_session, get_redis
from app.main import app
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, DepartmentPermission, Operator
from backend_core.auth.security import hash_password
from backend_core.config import get_settings
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.phase3a_auth_database import database_session


async def _login_and_select(
    client: AsyncClient,
    *,
    department: Department,
    operator: Operator,
    password: str,
) -> str:
    settings = get_settings()
    login = await client.post(
        "/api/v1/auth/login",
        json={"department_id": str(department.id), "password": password},
    )
    assert login.status_code == 200
    csrf = client.cookies.get(settings.csrf_cookie_name)
    assert csrf is not None
    selected = await client.post(
        "/api/v1/auth/select-operator",
        json={"operator_id": str(operator.id)},
        headers={"X-CSRF-Token": csrf},
    )
    assert selected.status_code == 200
    return csrf


def test_operator_admin_http_enforces_effective_role_and_department_scope() -> None:
    async def scenario() -> None:
        password = "operator-admin-http-password"
        fake_redis = FakeRedis(decode_responses=True)
        async with database_session() as session:
            department = Department(
                name="Operator Admin HTTP",
                password_hash=hash_password(password),
                status=DepartmentStatus.ACTIVE,
                session_days=30,
            )
            other_department = Department(
                name="Operator Admin HTTP Other",
                password_hash=hash_password(password),
                status=DepartmentStatus.ACTIVE,
                session_days=30,
            )
            session.add_all((department, other_department))
            await session.flush()
            super_admin = Operator(
                department_id=department.id,
                name="HTTP Super Admin",
                role=Role.SUPER_ADMIN,
                status=OperatorStatus.ACTIVE,
            )
            viewer = Operator(
                department_id=department.id,
                name="HTTP Viewer",
                role=Role.VIEWER,
                status=OperatorStatus.ACTIVE,
            )
            target = Operator(
                department_id=department.id,
                name="HTTP Target",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
            )
            other_super_admin = Operator(
                department_id=other_department.id,
                name="HTTP Other Super Admin",
                role=Role.SUPER_ADMIN,
                status=OperatorStatus.ACTIVE,
            )
            session.add_all(
                (
                    DepartmentPermission(department_id=department.id, role=Role.SUPER_ADMIN),
                    DepartmentPermission(
                        department_id=other_department.id,
                        role=Role.SUPER_ADMIN,
                    ),
                    super_admin,
                    viewer,
                    target,
                    other_super_admin,
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
                    async with AsyncClient(
                        transport=transport,
                        base_url="http://test",
                    ) as admin_client:
                        csrf = await _login_and_select(
                            admin_client,
                            department=department,
                            operator=super_admin,
                            password=password,
                        )
                        listed = await admin_client.get("/api/v1/admin/operators")
                        assert listed.status_code == 200
                        assert [item["name"] for item in listed.json()["data"]] == [
                            "HTTP Super Admin",
                            "HTTP Target",
                            "HTTP Viewer",
                        ]
                        assert listed.json()["data"][0]["module_grants"] == []

                        detail = await admin_client.get(f"/api/v1/admin/operators/{target.id}")
                        assert detail.status_code == 200
                        assert detail.json()["data"]["id"] == str(target.id)

                        created = await admin_client.post(
                            "/api/v1/admin/operators",
                            json={
                                "name": "HTTP Created",
                                "role": "operator",
                                "module_grants": ["data_updates", "campaigns", "campaigns"],
                            },
                            headers={"X-CSRF-Token": csrf},
                        )
                        assert created.status_code == 200
                        created_data = created.json()["data"]
                        assert created_data["module_grants"] == ["campaigns", "data_updates"]

                        patched = await admin_client.patch(
                            f"/api/v1/admin/operators/{created_data['id']}",
                            json={
                                "expected_updated_at": created_data["updated_at"],
                                "module_grants": ["influencer_library"],
                            },
                            headers={"X-CSRF-Token": csrf},
                        )
                        assert patched.status_code == 200
                        assert patched.json()["data"]["module_grants"] == ["influencer_library"]

                        stale = await admin_client.patch(
                            f"/api/v1/admin/operators/{created_data['id']}",
                            json={
                                "expected_updated_at": created_data["updated_at"],
                                "name": "Stale HTTP Update",
                            },
                            headers={"X-CSRF-Token": csrf},
                        )
                        assert stale.status_code == 409
                        assert stale.json()["error"]["code"] == "VERSION_CONFLICT"

                        invalid_grant = await admin_client.post(
                            "/api/v1/admin/operators",
                            json={
                                "name": "HTTP Invalid Grant",
                                "role": "operator",
                                "module_grants": ["admin"],
                            },
                            headers={"X-CSRF-Token": csrf},
                        )
                        assert invalid_grant.status_code == 422
                        assert invalid_grant.json()["error"]["code"] == "ADMIN_MODULE_NOT_GRANTABLE"
                        # A rejected mutation rolls back the shared test
                        # session, so refresh models before the next request.
                        await session.refresh(department)
                        await session.refresh(other_department)
                        await session.refresh(super_admin)
                        await session.refresh(viewer)
                        await session.refresh(target)
                        await session.refresh(other_super_admin)

                    async with AsyncClient(
                        transport=transport,
                        base_url="http://test",
                    ) as unselected_client:
                        login = await unselected_client.post(
                            "/api/v1/auth/login",
                            json={"department_id": str(department.id), "password": password},
                        )
                        assert login.status_code == 200
                        unselected = await unselected_client.get("/api/v1/admin/operators")
                        assert unselected.status_code == 409
                        assert unselected.json()["error"]["code"] == "OPERATOR_REQUIRED"

                    async with AsyncClient(
                        transport=transport,
                        base_url="http://test",
                    ) as viewer_client:
                        await _login_and_select(
                            viewer_client,
                            department=department,
                            operator=viewer,
                            password=password,
                        )
                        denied = await viewer_client.get("/api/v1/admin/operators")
                        assert denied.status_code == 403
                        assert denied.json()["error"]["code"] == "PERMISSION_DENIED"

                    async with AsyncClient(
                        transport=transport,
                        base_url="http://test",
                    ) as other_client:
                        await _login_and_select(
                            other_client,
                            department=other_department,
                            operator=other_super_admin,
                            password=password,
                        )
                        concealed = await other_client.get(f"/api/v1/admin/operators/{target.id}")
                        assert concealed.status_code == 404
                        assert concealed.json()["error"]["code"] == "OPERATOR_NOT_FOUND"
            finally:
                app.dependency_overrides.clear()

        await fake_redis.aclose()

    asyncio.run(scenario())
