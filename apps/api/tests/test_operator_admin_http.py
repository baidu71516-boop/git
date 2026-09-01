"""Focused HTTP contracts for Permissions V1 Operator administration."""

import asyncio
from collections.abc import AsyncIterator

from app.http.dependencies import get_database_session, get_redis
from app.main import app
from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import Department, DepartmentPermission, Operator
from backend_core.auth.security import hash_password, verify_password
from backend_core.config import get_settings
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fixtures.phase3a_auth_database import database_session


async def _login_and_select(
    client: AsyncClient,
    *,
    department: Department,
    operator: Operator,
    department_password: str,
    operator_password: str,
) -> str:
    settings = get_settings()
    login = await client.post(
        "/api/v1/auth/login",
        json={"department_id": str(department.id), "password": department_password},
    )
    assert login.status_code == 200
    csrf = client.cookies.get(settings.csrf_cookie_name)
    assert csrf is not None
    selected = await client.post(
        "/api/v1/auth/select-operator",
        json={
            "operator_id": str(operator.id),
            "operator_password": operator_password,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert selected.status_code == 200
    rotated_csrf = client.cookies.get(settings.csrf_cookie_name)
    assert rotated_csrf is not None
    assert rotated_csrf != csrf
    return rotated_csrf


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
                password_hash=hash_password("http-super-admin-password"),
                credential_version=1,
            )
            viewer = Operator(
                department_id=department.id,
                name="HTTP Viewer",
                role=Role.VIEWER,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("http-viewer-password"),
                credential_version=1,
            )
            target = Operator(
                department_id=department.id,
                name="HTTP Target",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("http-target-password"),
                credential_version=1,
            )
            other_super_admin = Operator(
                department_id=other_department.id,
                name="HTTP Other Super Admin",
                role=Role.SUPER_ADMIN,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("http-other-admin-password"),
                credential_version=1,
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
                            department_password=password,
                            operator_password="http-super-admin-password",
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

                        create_without_csrf = await admin_client.post(
                            "/api/v1/admin/operators",
                            json={
                                "name": "HTTP No CSRF",
                                "password": "http-no-csrf-password",
                                "role": "operator",
                                "module_grants": [],
                            },
                        )
                        assert create_without_csrf.status_code == 403
                        assert create_without_csrf.json()["error"]["code"] == "CSRF_FAILED"

                        missing_create_password = await admin_client.post(
                            "/api/v1/admin/operators",
                            json={
                                "name": "HTTP Missing Password",
                                "role": "operator",
                                "module_grants": [],
                            },
                            headers={"X-CSRF-Token": csrf},
                        )
                        assert missing_create_password.status_code == 422

                        created = await admin_client.post(
                            "/api/v1/admin/operators",
                            json={
                                "name": "HTTP Created",
                                "password": "http-created-password",
                                "role": "operator",
                                "module_grants": ["data_updates", "campaigns", "campaigns"],
                            },
                            headers={"X-CSRF-Token": csrf},
                        )
                        assert created.status_code == 200
                        assert "http-created-password" not in created.text
                        assert "password_hash" not in created.text
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
                                "password": "http-invalid-grant-password",
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

                        target_before = {
                            "department_id": target.department_id,
                            "name": target.name,
                            "role": target.role,
                            "status": target.status,
                            "created_at": target.created_at,
                            "credential_version": target.credential_version,
                            "password_hash": target.password_hash,
                        }
                        async with AsyncClient(
                            transport=transport,
                            base_url="http://test",
                        ) as target_client:
                            await _login_and_select(
                                target_client,
                                department=department,
                                operator=target,
                                department_password=password,
                                operator_password="http-target-password",
                            )
                            assert (await target_client.get("/api/v1/auth/me")).status_code == 200
                            reset_without_csrf = await admin_client.post(
                                f"/api/v1/admin/operators/{target.id}/reset-password",
                                json={"password": "http-no-csrf-reset"},
                            )
                            assert reset_without_csrf.status_code == 403
                            assert reset_without_csrf.json()["error"]["code"] == "CSRF_FAILED"
                            assert (await target_client.get("/api/v1/auth/me")).status_code == 200
                            reset = await admin_client.post(
                                f"/api/v1/admin/operators/{target.id}/reset-password",
                                json={"password": "http-target-replacement"},
                                headers={"X-CSRF-Token": csrf},
                            )
                            assert reset.status_code == 200
                            assert reset.json()["data"] == {
                                "operator_id": str(target.id),
                                "revoked_sessions": 1,
                            }
                            assert "http-target-replacement" not in reset.text
                            revoked_me = await target_client.get("/api/v1/auth/me")
                            assert revoked_me.status_code == 401
                            assert revoked_me.json()["error"]["code"] == "INVALID_SESSION"

                        await session.refresh(target)
                        assert target.password_hash is not None
                        assert target.password_hash != target_before["password_hash"]
                        assert verify_password(target.password_hash, "http-target-replacement")
                        assert target.credential_version == target_before["credential_version"] + 1
                        assert target.department_id == target_before["department_id"]
                        assert target.name == target_before["name"]
                        assert target.role is target_before["role"]
                        assert target.status is target_before["status"]
                        assert target.created_at == target_before["created_at"]

                        async with AsyncClient(
                            transport=transport,
                            base_url="http://test",
                        ) as reset_verify_client:
                            reset_login = await reset_verify_client.post(
                                "/api/v1/auth/login",
                                json={
                                    "department_id": str(department.id),
                                    "password": password,
                                },
                            )
                            assert reset_login.status_code == 200
                            reset_csrf = reset_verify_client.cookies.get(
                                get_settings().csrf_cookie_name
                            )
                            assert reset_csrf is not None
                            old_password = await reset_verify_client.post(
                                "/api/v1/auth/select-operator",
                                json={
                                    "operator_id": str(target.id),
                                    "operator_password": "http-target-password",
                                },
                                headers={"X-CSRF-Token": reset_csrf},
                            )
                            assert old_password.status_code == 401
                            assert old_password.json()["error"]["code"] == (
                                "INVALID_OPERATOR_CREDENTIALS"
                            )
                            new_password = await reset_verify_client.post(
                                "/api/v1/auth/select-operator",
                                json={
                                    "operator_id": str(target.id),
                                    "operator_password": "http-target-replacement",
                                },
                                headers={"X-CSRF-Token": reset_csrf},
                            )
                            assert new_password.status_code == 200

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
                            department_password=password,
                            operator_password="http-viewer-password",
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
                            department_password=password,
                            operator_password="http-other-admin-password",
                        )
                        concealed = await other_client.get(f"/api/v1/admin/operators/{target.id}")
                        assert concealed.status_code == 404
                        assert concealed.json()["error"]["code"] == "OPERATOR_NOT_FOUND"

                    password_audit = await session.scalar(
                        select(AuditLog).where(
                            AuditLog.action == AuditAction.OPERATOR_PASSWORD_RESET
                        )
                    )
                    assert password_audit is not None
                    assert password_audit.entity_id == target.id
                    assert password_audit.after == {"revoked_sessions": 1}
                    audit_payload = repr((password_audit.before, password_audit.after))
                    assert "http-target-password" not in audit_payload
                    assert "http-target-replacement" not in audit_payload
            finally:
                app.dependency_overrides.clear()

        await fake_redis.aclose()

    asyncio.run(scenario())
