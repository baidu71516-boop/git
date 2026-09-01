import asyncio
from collections.abc import AsyncIterator

from app.http.dependencies import get_database_session, get_redis
from app.main import app
from backend_core.audit.enums import AuditAction
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.auth.security import hash_password, hash_token
from backend_core.config import get_settings
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
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
                password_hash=hash_password("viewer-operator-password"),
                credential_version=1,
            )
            below_ceiling_operator = Operator(
                department_id=department.id,
                name="Operator Below Super Admin Ceiling",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("below-ceiling-password"),
                credential_version=1,
            )
            other_credential_operator = Operator(
                department_id=department.id,
                name="Other Credential Owner",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("other-operator-password"),
                credential_version=1,
            )
            setup_required_operator = Operator(
                department_id=department.id,
                name="Setup Required Operator",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
                password_hash=None,
                credential_version=0,
            )
            session.add_all(
                [
                    DepartmentPermission(department_id=department.id, role=Role.SUPER_ADMIN),
                    operator,
                    below_ceiling_operator,
                    other_credential_operator,
                    setup_required_operator,
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
                        assert login.json()["data"]["role"] == "super_admin"
                        assert login.json()["data"]["department_role_ceiling"] == "super_admin"
                        assert login.json()["data"]["effective_role"] is None
                        assert "session_token" not in login.text
                        session_set_cookie = login.headers.get_list("set-cookie")[0].lower()
                        assert "httponly" in session_set_cookie
                        assert "samesite=lax" in session_set_cookie
                        login_session_token = client.cookies.get(settings.session_cookie_name)
                        login_csrf_token = client.cookies.get(settings.csrf_cookie_name)
                        assert login_session_token is not None
                        assert login_csrf_token is not None

                        before_selection = await client.get("/api/v1/auth/me")
                        assert before_selection.status_code == 200
                        assert before_selection.json()["data"]["operator"] is None
                        assert (
                            before_selection.json()["data"]["department_role_ceiling"]
                            == "super_admin"
                        )
                        assert before_selection.json()["data"]["effective_role"] is None

                        business_before_selection = await client.get("/api/v1/influencers")
                        assert business_before_selection.status_code == 409
                        assert (
                            business_before_selection.json()["error"]["code"] == "OPERATOR_REQUIRED"
                        )

                        csrf_rejected = await client.post(
                            "/api/v1/auth/select-operator",
                            json={
                                "operator_id": str(operator.id),
                                "operator_password": "viewer-operator-password",
                            },
                        )
                        assert csrf_rejected.status_code == 403
                        assert csrf_rejected.json()["error"]["code"] == "CSRF_FAILED"

                        omitted_password = await client.post(
                            "/api/v1/auth/select-operator",
                            json={"operator_id": str(operator.id)},
                            headers={"X-CSRF-Token": login_csrf_token},
                        )
                        assert omitted_password.status_code == 422
                        assert "viewer-operator-password" not in omitted_password.text

                        extra_role = await client.post(
                            "/api/v1/auth/select-operator",
                            json={
                                "operator_id": str(operator.id),
                                "operator_password": "viewer-operator-password",
                                "role": "super_admin",
                            },
                            headers={"X-CSRF-Token": login_csrf_token},
                        )
                        assert extra_role.status_code == 422
                        assert "viewer-operator-password" not in extra_role.text

                        for rejected_operator_password in (
                            "wrong-operator-password",
                            "http-test-password",
                            "other-operator-password",
                        ):
                            rejected = await client.post(
                                "/api/v1/auth/select-operator",
                                json={
                                    "operator_id": str(operator.id),
                                    "operator_password": rejected_operator_password,
                                },
                                headers={"X-CSRF-Token": login_csrf_token},
                            )
                            assert rejected.status_code == 401
                            assert rejected.json()["error"]["code"] == (
                                "INVALID_OPERATOR_CREDENTIALS"
                            )
                            assert rejected_operator_password not in rejected.text
                            assert (
                                client.cookies.get(settings.session_cookie_name)
                                == login_session_token
                            )

                        setup_required = await client.post(
                            "/api/v1/auth/select-operator",
                            json={
                                "operator_id": str(setup_required_operator.id),
                                "operator_password": "uninitialized-operator-password",
                            },
                            headers={"X-CSRF-Token": login_csrf_token},
                        )
                        assert setup_required.status_code == 409
                        assert setup_required.json()["error"]["code"] == (
                            "CREDENTIAL_SETUP_REQUIRED"
                        )

                        selected = await client.post(
                            "/api/v1/auth/select-operator",
                            json={
                                "operator_id": str(operator.id),
                                "operator_password": "viewer-operator-password",
                            },
                            headers={"X-CSRF-Token": login_csrf_token},
                        )
                        assert selected.status_code == 200
                        assert "viewer-operator-password" not in selected.text
                        assert selected.json()["data"]["operator"]["id"] == str(operator.id)
                        # The legacy role is a Department ceiling. Current authority is
                        # only the separately resolved, persisted Operator role.
                        assert selected.json()["data"]["role"] == "super_admin"
                        assert selected.json()["data"]["department_role_ceiling"] == "super_admin"
                        assert selected.json()["data"]["effective_role"] == "viewer"
                        rotated_session_token = client.cookies.get(settings.session_cookie_name)
                        rotated_csrf_token = client.cookies.get(settings.csrf_cookie_name)
                        assert rotated_session_token is not None
                        assert rotated_csrf_token is not None
                        assert rotated_session_token != login_session_token
                        assert rotated_csrf_token != login_csrf_token
                        assert len(selected.headers.get_list("set-cookie")) == 2

                        async with AsyncClient(
                            transport=transport,
                            base_url="http://test",
                            cookies={settings.session_cookie_name: login_session_token},
                        ) as replay_client:
                            old_token_replay = await replay_client.get("/api/v1/auth/me")
                            assert old_token_replay.status_code == 401
                            assert old_token_replay.json()["error"]["code"] == "INVALID_SESSION"

                        selected_me = await client.get("/api/v1/auth/me")
                        assert selected_me.status_code == 200
                        assert (
                            selected_me.json()["data"]["department_role_ceiling"] == "super_admin"
                        )
                        assert selected_me.json()["data"]["effective_role"] == "viewer"

                        stale_csrf = await client.post(
                            "/api/v1/auth/select-operator",
                            json={
                                "operator_id": str(below_ceiling_operator.id),
                                "operator_password": "below-ceiling-password",
                            },
                            headers={"X-CSRF-Token": login_csrf_token},
                        )
                        assert stale_csrf.status_code == 403
                        assert stale_csrf.json()["error"]["code"] == "CSRF_FAILED"

                        selected_operator = await client.post(
                            "/api/v1/auth/select-operator",
                            json={
                                "operator_id": str(below_ceiling_operator.id),
                                "operator_password": "below-ceiling-password",
                            },
                            headers={"X-CSRF-Token": rotated_csrf_token},
                        )
                        assert selected_operator.status_code == 200
                        assert (
                            selected_operator.json()["data"]["department_role_ceiling"]
                            == "super_admin"
                        )
                        assert selected_operator.json()["data"]["effective_role"] == "operator"
                        final_csrf_token = client.cookies.get(settings.csrf_cookie_name)
                        assert final_csrf_token is not None
                        assert final_csrf_token != rotated_csrf_token

                        denied_admin = await client.post(
                            f"/api/v1/admin/departments/{department.id}/reset-password",
                            json={"new_password": "replacement-password"},
                            headers={"X-CSRF-Token": final_csrf_token},
                        )
                        assert denied_admin.status_code == 403
                        assert denied_admin.json()["error"]["code"] == "PERMISSION_DENIED"

                        logout = await client.post(
                            "/api/v1/auth/logout",
                            headers={"X-CSRF-Token": final_csrf_token},
                        )
                        assert logout.status_code == 200
                        after_logout = await client.get("/api/v1/auth/me")
                        assert after_logout.status_code == 401

                    async def bind_version_client(client: AsyncClient) -> tuple[str, AuthSession]:
                        version_login = await client.post(
                            "/api/v1/auth/login",
                            json={
                                "department_id": str(department.id),
                                "password": "http-test-password",
                            },
                        )
                        assert version_login.status_code == 200
                        version_csrf = client.cookies.get(settings.csrf_cookie_name)
                        assert version_csrf is not None
                        version_selected = await client.post(
                            "/api/v1/auth/select-operator",
                            json={
                                "operator_id": str(operator.id),
                                "operator_password": "viewer-operator-password",
                            },
                            headers={"X-CSRF-Token": version_csrf},
                        )
                        assert version_selected.status_code == 200
                        version_token = client.cookies.get(settings.session_cookie_name)
                        assert version_token is not None
                        bound = await session.scalar(
                            select(AuthSession).where(
                                AuthSession.token_hash == hash_token(version_token)
                            )
                        )
                        assert bound is not None
                        return version_token, bound

                    async with AsyncClient(
                        transport=transport,
                        base_url="http://test",
                    ) as legacy_version_client:
                        _legacy_token, legacy_bound = await bind_version_client(
                            legacy_version_client
                        )
                        legacy_bound.operator_credential_version = None
                        await session.commit()
                        legacy_me = await legacy_version_client.get("/api/v1/auth/me")
                        assert legacy_me.status_code == 401
                        assert legacy_me.json()["error"]["code"] == "INVALID_SESSION"
                        await session.refresh(legacy_bound)
                        assert legacy_bound.revoked_at is not None

                    async with AsyncClient(
                        transport=transport,
                        base_url="http://test",
                    ) as mismatch_client:
                        _mismatch_token, mismatch_bound = await bind_version_client(mismatch_client)
                        current_operator = await session.get(Operator, operator.id)
                        assert current_operator is not None
                        current_operator.credential_version += 1
                        await session.commit()
                        mismatch_me = await mismatch_client.get("/api/v1/auth/me")
                        assert mismatch_me.status_code == 401
                        assert mismatch_me.json()["error"]["code"] == "INVALID_SESSION"
                        await session.refresh(mismatch_bound)
                        assert mismatch_bound.revoked_at is not None

                    audit_payload = repr(
                        [
                            (audit.action.value, audit.before, audit.after)
                            for audit in await session.scalars(
                                select(AuditLog).where(
                                    AuditLog.action.in_(
                                        (
                                            AuditAction.OPERATOR_AUTH_FAILED,
                                            AuditAction.OPERATOR_AUTHENTICATED,
                                        )
                                    )
                                )
                            )
                        ]
                    )
                    for secret in (
                        "viewer-operator-password",
                        "wrong-operator-password",
                        "http-test-password",
                        "other-operator-password",
                        "uninitialized-operator-password",
                    ):
                        assert secret not in audit_payload
            finally:
                app.dependency_overrides.clear()

        await fake_redis.aclose()

    asyncio.run(scenario())


def test_super_admin_operator_http_requires_exact_target_password() -> None:
    async def scenario() -> None:
        settings = get_settings()
        fake_redis = FakeRedis(decode_responses=True)
        async with database_session() as session:
            department = Department(
                name="HTTP Super Admin Attack",
                password_hash=hash_password("department-only-password"),
                status=DepartmentStatus.ACTIVE,
                session_days=30,
            )
            cross_department = Department(
                name="HTTP Super Admin Cross Department",
                password_hash=hash_password("cross-department-password"),
                status=DepartmentStatus.ACTIVE,
                session_days=30,
            )
            session.add_all((department, cross_department))
            await session.flush()
            target = Operator(
                department_id=department.id,
                name="Exact Super Admin",
                role=Role.SUPER_ADMIN,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("exact-super-admin-password"),
                credential_version=1,
            )
            other = Operator(
                department_id=department.id,
                name="Different Operator",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("different-operator-password"),
                credential_version=1,
            )
            disabled = Operator(
                department_id=department.id,
                name="Disabled Super Admin",
                role=Role.SUPER_ADMIN,
                status=OperatorStatus.DISABLED,
                password_hash=hash_password("disabled-super-admin-password"),
                credential_version=1,
            )
            cross_department_target = Operator(
                department_id=cross_department.id,
                name="Cross Department Super Admin",
                role=Role.SUPER_ADMIN,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("cross-super-admin-password"),
                credential_version=1,
            )
            session.add_all(
                (
                    DepartmentPermission(department_id=department.id, role=Role.SUPER_ADMIN),
                    DepartmentPermission(
                        department_id=cross_department.id,
                        role=Role.SUPER_ADMIN,
                    ),
                    target,
                    other,
                    disabled,
                    cross_department_target,
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
                        login = await client.post(
                            "/api/v1/auth/login",
                            json={
                                "department_id": str(department.id),
                                "password": "department-only-password",
                            },
                        )
                        assert login.status_code == 200
                        old_session_token = client.cookies.get(settings.session_cookie_name)
                        csrf = client.cookies.get(settings.csrf_cookie_name)
                        assert old_session_token is not None
                        assert csrf is not None

                        omitted = await client.post(
                            "/api/v1/auth/select-operator",
                            json={"operator_id": str(target.id)},
                            headers={"X-CSRF-Token": csrf},
                        )
                        assert omitted.status_code == 422

                        extra_role = await client.post(
                            "/api/v1/auth/select-operator",
                            json={
                                "operator_id": str(target.id),
                                "operator_password": "exact-super-admin-password",
                                "role": "super_admin",
                            },
                            headers={"X-CSRF-Token": csrf},
                        )
                        assert extra_role.status_code == 422
                        assert "exact-super-admin-password" not in extra_role.text

                        for rejected_password in (
                            "wrong-super-admin-password",
                            "department-only-password",
                            "different-operator-password",
                        ):
                            rejected = await client.post(
                                "/api/v1/auth/select-operator",
                                json={
                                    "operator_id": str(target.id),
                                    "operator_password": rejected_password,
                                },
                                headers={
                                    "X-CSRF-Token": csrf,
                                    "X-Forwarded-For": "192.0.2.220",
                                },
                            )
                            assert rejected.status_code == 401
                            assert rejected.json()["error"]["code"] == (
                                "INVALID_OPERATOR_CREDENTIALS"
                            )
                            assert rejected_password not in rejected.text
                            assert (
                                client.cookies.get(settings.session_cookie_name)
                                == old_session_token
                            )

                        for rejected_target, rejected_password in (
                            (disabled, "disabled-super-admin-password"),
                            (cross_department_target, "cross-super-admin-password"),
                        ):
                            rejected_identity = await client.post(
                                "/api/v1/auth/select-operator",
                                json={
                                    "operator_id": str(rejected_target.id),
                                    "operator_password": rejected_password,
                                },
                                headers={
                                    "X-CSRF-Token": csrf,
                                    "X-Forwarded-For": "192.0.2.221",
                                },
                            )
                            assert rejected_identity.status_code == 401
                            assert rejected_identity.json()["error"]["code"] == (
                                "INVALID_OPERATOR_CREDENTIALS"
                            )
                            assert rejected_password not in rejected_identity.text
                            assert (
                                client.cookies.get(settings.session_cookie_name)
                                == old_session_token
                            )

                        selected = await client.post(
                            "/api/v1/auth/select-operator",
                            json={
                                "operator_id": str(target.id),
                                "operator_password": "exact-super-admin-password",
                            },
                            headers={
                                "X-CSRF-Token": csrf,
                                "X-Forwarded-For": "192.0.2.222",
                            },
                        )
                        assert selected.status_code == 200
                        assert selected.json()["data"]["operator"]["id"] == str(target.id)
                        assert selected.json()["data"]["effective_role"] == "super_admin"
                        assert "exact-super-admin-password" not in selected.text
                        bound_me = await client.get("/api/v1/auth/me")
                        assert bound_me.status_code == 200
                        assert bound_me.json()["data"]["operator"]["id"] == str(target.id)
                        assert bound_me.json()["data"]["effective_role"] == "super_admin"

                        async with AsyncClient(
                            transport=transport,
                            base_url="http://test",
                            cookies={settings.session_cookie_name: old_session_token},
                        ) as replay_client:
                            replay = await replay_client.get("/api/v1/auth/me")
                            assert replay.status_code == 401
                            assert replay.json()["error"]["code"] == "INVALID_SESSION"

                    audit_payload = repr(
                        [
                            (audit.action.value, audit.before, audit.after)
                            for audit in await session.scalars(
                                select(AuditLog).where(
                                    AuditLog.action.in_(
                                        (
                                            AuditAction.OPERATOR_AUTH_FAILED,
                                            AuditAction.OPERATOR_AUTHENTICATED,
                                        )
                                    )
                                )
                            )
                        ]
                    )
                    for secret in (
                        "wrong-super-admin-password",
                        "department-only-password",
                        "different-operator-password",
                        "disabled-super-admin-password",
                        "cross-super-admin-password",
                        "exact-super-admin-password",
                    ):
                        assert secret not in audit_payload
            finally:
                app.dependency_overrides.clear()

        await fake_redis.aclose()

    asyncio.run(scenario())


def test_operator_directory_stays_same_department_without_selected_operator() -> None:
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
                password_hash=hash_password("super-operator-password"),
                credential_version=1,
            )
            target_active = Operator(
                department_id=target_department.id,
                name="Target Active",
                role=Role.OPERATOR,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("target-active-password"),
                credential_version=1,
            )
            target_disabled = Operator(
                department_id=target_department.id,
                name="Target Disabled",
                role=Role.OPERATOR,
                status=OperatorStatus.DISABLED,
                password_hash=hash_password("target-disabled-password"),
                credential_version=1,
            )
            normal_operator = Operator(
                department_id=normal_department.id,
                name="Normal Operator",
                role=Role.SUPER_ADMIN,
                status=OperatorStatus.ACTIVE,
                password_hash=hash_password("normal-operator-password"),
                credential_version=1,
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

                        ignored_cross_department_header = await client.get(
                            "/api/v1/operators",
                            headers={"X-Department-ID": str(target_department.id)},
                        )
                        assert ignored_cross_department_header.status_code == 200
                        assert ignored_cross_department_header.json()["data"] == [
                            {
                                "id": str(super_operator.id),
                                "department_id": str(super_department.id),
                                "name": super_operator.name,
                                "role": "operator",
                                "status": "active",
                            }
                        ]

                        ignored_disabled_department_header = await client.get(
                            "/api/v1/operators",
                            headers={"X-Department-ID": str(disabled_department.id)},
                        )
                        assert ignored_disabled_department_header.status_code == 200
                        assert (
                            ignored_disabled_department_header.json()["data"] == own.json()["data"]
                        )

                        ignored_invalid_header = await client.get(
                            "/api/v1/operators",
                            headers={"X-Department-ID": "not-a-uuid"},
                        )
                        assert ignored_invalid_header.status_code == 200
                        assert ignored_invalid_header.json()["data"] == own.json()["data"]
                        ignored_duplicate_header = await client.get(
                            "/api/v1/operators",
                            headers=[
                                ("X-Department-ID", str(target_department.id)),
                                ("X-Department-ID", str(target_department.id)),
                            ],
                        )
                        assert ignored_duplicate_header.status_code == 200
                        assert ignored_duplicate_header.json()["data"] == own.json()["data"]

                        normal_login = await client.post(
                            "/api/v1/auth/login",
                            json={
                                "department_id": str(normal_department.id),
                                "password": "normal-password",
                            },
                        )
                        assert normal_login.status_code == 200
                        # The bootstrap selection directory remains usable with
                        # a valid session before an Operator is selected.
                        normal_own = await client.get("/api/v1/operators")
                        assert normal_own.status_code == 200
                        assert [item["id"] for item in normal_own.json()["data"]] == [
                            str(normal_operator.id)
                        ]
                        normal_ignored_cross_header = await client.get(
                            "/api/v1/operators",
                            headers={"X-Department-ID": str(target_department.id)},
                        )
                        assert normal_ignored_cross_header.status_code == 200
                        assert (
                            normal_ignored_cross_header.json()["data"] == normal_own.json()["data"]
                        )

                    document = app.openapi()
                    operation = document["paths"]["/api/v1/operators"]["get"]
                    assert not any(
                        parameter["in"] == "header" and parameter["name"] == "X-Department-ID"
                        for parameter in operation.get("parameters", [])
                    )
                    assert "OperatorPublic" in str(
                        operation["responses"]["200"]["content"]["application/json"]["schema"]
                    )
            finally:
                app.dependency_overrides.clear()

        await fake_redis.aclose()
        await engine.dispose()

    asyncio.run(scenario())
