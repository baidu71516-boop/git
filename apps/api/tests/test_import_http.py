import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from app.http.dependencies import (
    get_database_session,
    get_import_storage,
    get_import_task_dispatcher,
    get_redis,
    require_auth,
    require_csrf_context,
)
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
from backend_core.auth.service import AuthContext
from backend_core.config import get_settings
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.enums import (
    ImportJobFileStatus,
    ImportJobStatus,
    ImportMatchType,
    ImportRowAction,
    ImportTaskState,
    SourceAcquiredAtOrigin,
)
from backend_core.imports.models import ImportJob, ImportJobFile, ImportRow, ImportTaskRequest
from backend_core.imports.storage import LocalStorageAdapter
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class FakeDispatcher:
    def __init__(self) -> None:
        self.parse_calls: list[tuple[UUID, str]] = []
        self.confirm_calls: list[tuple[UUID, int, str]] = []
        self.fail_parse = False

    async def parse(self, import_job_id: UUID, task_id: str) -> None:
        self.parse_calls.append((import_job_id, task_id))
        if self.fail_parse:
            raise RuntimeError("synthetic broker failure")

    async def confirm(self, import_job_id: UUID, preview_revision: int, task_id: str) -> None:
        self.confirm_calls.append((import_job_id, preview_revision, task_id))


async def seed_context(
    session: AsyncSession,
    *,
    department_name: str,
    role: Role,
    operator_selected: bool = True,
) -> AuthContext:
    department = Department(
        name=department_name,
        password_hash="not-used",
        status=DepartmentStatus.ACTIVE,
        session_days=30,
    )
    session.add(department)
    await session.flush()
    operator = Operator(
        department_id=department.id,
        name=f"{department_name} 操作人",
        role=role,
        status=OperatorStatus.ACTIVE,
        password_hash=(
            hash_password(f"{department_name}-operator-test-password")
            if operator_selected
            else None
        ),
        credential_version=1 if operator_selected else 0,
    )
    session.add(operator)
    await session.flush()
    auth_session = AuthSession(
        department_id=department.id,
        operator_id=operator.id if operator_selected else None,
        operator_credential_version=1 if operator_selected else None,
        token_hash=hash_token(f"session-{department_name}"),
        csrf_token_hash=hash_token(f"csrf-{department_name}"),
        ip="127.0.0.1",
        user_agent="test",
        expires_at=datetime.now(UTC) + timedelta(hours=12),
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
    await session.commit()
    return AuthContext(
        department=department,
        operator=operator if operator_selected else None,
        role=role,
        auth_session=auth_session,
    )


def valid_csv() -> bytes:
    return (
        "达人名称,达人官方地址\n" "脱敏达人,https://www.xiaohongshu.com/user/profile/http-fixture\n"
    ).encode("utf-8-sig")


async def complete_task(session: AsyncSession, task_id: str) -> None:
    task = await session.scalar(
        select(ImportTaskRequest).where(ImportTaskRequest.task_token == UUID(task_id))
    )
    assert task is not None
    task.state = ImportTaskState.COMPLETED
    task.completed_at = datetime.now(UTC)
    task.next_retry_at = None
    task.lease_expires_at = None


def test_import_http_flow_rbac_scope_and_idempotent_dispatch() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        fake_redis = FakeRedis(decode_responses=True)
        dispatcher = FakeDispatcher()
        with TemporaryDirectory() as directory:
            storage = LocalStorageAdapter(Path(directory))
            async with factory() as session:
                operator_context = await seed_context(
                    session, department_name="HTTP Operator", role=Role.OPERATOR
                )
                viewer_context = await seed_context(
                    session, department_name="HTTP Viewer", role=Role.VIEWER
                )
                admin_context = await seed_context(
                    session, department_name="HTTP Admin", role=Role.SUPER_ADMIN
                )
                no_operator_context = await seed_context(
                    session,
                    department_name="HTTP No Operator",
                    role=Role.MANAGER,
                    operator_selected=False,
                )

                async def override_database_session() -> AsyncIterator[AsyncSession]:
                    yield session

                def override_redis() -> FakeRedis:
                    return fake_redis

                def override_storage() -> LocalStorageAdapter:
                    return storage

                def override_dispatcher() -> FakeDispatcher:
                    return dispatcher

                async def operator_auth() -> AuthContext:
                    return operator_context

                app.dependency_overrides[get_database_session] = override_database_session
                app.dependency_overrides[get_redis] = override_redis
                app.dependency_overrides[get_import_storage] = override_storage
                app.dependency_overrides[get_import_task_dispatcher] = override_dispatcher
                app.dependency_overrides[require_auth] = operator_auth
                app.dependency_overrides[require_csrf_context] = operator_auth
                try:
                    async with app.router.lifespan_context(app):
                        transport = ASGITransport(app=app)
                        async with AsyncClient(
                            transport=transport, base_url="http://test"
                        ) as client:
                            client.cookies.set(
                                get_settings().csrf_cookie_name,
                                "csrf-HTTP Operator",
                            )
                            client.headers["X-CSRF-Token"] = "csrf-HTTP Operator"
                            created = await client.post(
                                "/api/v1/collection-jobs",
                                json={
                                    "name": "HTTP 脱敏任务",
                                    "industry": "测试",
                                    "purpose": "验证 API",
                                    "target_action": "Preview",
                                    "target_count": 2,
                                    "source_type": "manual_huitun_export",
                                },
                            )
                            assert created.status_code == 201
                            collection_id = created.json()["data"]["id"]

                            uploaded = await client.post(
                                "/api/v1/import-jobs",
                                data={"collection_job_id": collection_id},
                                files={"file": ("fixture.csv", valid_csv(), "text/csv")},
                            )
                            assert uploaded.status_code == 202
                            assert uploaded.json()["data"]["status"] == "uploaded"
                            job_id = UUID(uploaded.json()["data"]["id"])
                            assert len(dispatcher.parse_calls) == 1
                            assert dispatcher.parse_calls[0][0] == job_id

                            job = await session.get(ImportJob, job_id)
                            assert job is not None
                            occurrence = await session.scalar(
                                select(ImportJobFile).where(ImportJobFile.import_job_id == job_id)
                            )
                            assert occurrence is not None
                            assert occurrence.position == 1
                            assert occurrence.stored_file_id == job.stored_file_id
                            assert occurrence.status is ImportJobFileStatus.UPLOADED
                            assert occurrence.source_acquired_at is None
                            assert (
                                occurrence.source_acquired_at_origin
                                is SourceAcquiredAtOrigin.LEGACY_UNKNOWN
                            )
                            assert job.parse_task_id is not None
                            await complete_task(session, job.parse_task_id)
                            job.status = ImportJobStatus.MAPPING_REQUIRED
                            job.detected_fields = ["name", "id"]
                            await session.commit()

                            mapped = await client.put(
                                f"/api/v1/import-jobs/{job_id}/mapping",
                                json={
                                    "mapping": {
                                        "name": "nickname",
                                        "id": "platform_account_id",
                                    }
                                },
                            )
                            assert mapped.status_code == 202
                            assert len(dispatcher.parse_calls) == 2
                            assert mapped.json()["data"]["task_id"] == dispatcher.parse_calls[-1][1]

                            assert job.parse_task_id is not None
                            await complete_task(session, job.parse_task_id)
                            job.status = ImportJobStatus.PREVIEW_READY
                            job.preview_revision = 1
                            job.preview_summary = {"total_rows": 1, "created_rows": 1}
                            row = ImportRow(
                                import_job_id=job.id,
                                import_job_file_id=occurrence.id,
                                row_number=2,
                                raw_data={"name": "脱敏"},
                                normalized_data={"display_name": "脱敏"},
                                match_type=ImportMatchType.NONE,
                                action=ImportRowAction.CREATE,
                                merge_plan={},
                                warnings=[],
                                errors=[],
                                preview_revision=1,
                                plan_hash="a" * 64,
                            )
                            session.add(row)
                            await session.commit()

                            page = await client.get(f"/api/v1/import-jobs/{job_id}/rows?limit=1")
                            assert page.status_code == 200
                            assert page.json()["data"]["total"] == 1
                            assert page.json()["data"]["items"][0]["row_number"] == 2

                            legacy_retry = await client.post(f"/api/v1/import-jobs/{job_id}/retry")
                            assert legacy_retry.status_code == 409
                            assert (
                                legacy_retry.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
                            )
                            assert len(dispatcher.parse_calls) == 2

                            confirmed = await client.post(
                                f"/api/v1/import-jobs/{job_id}/confirm",
                                json={"preview_revision": 1},
                            )
                            assert confirmed.status_code == 202
                            assert len(dispatcher.confirm_calls) == 1
                            assert (
                                confirmed.json()["data"]["task_id"]
                                == dispatcher.confirm_calls[-1][2]
                            )
                            confirm_task_id = confirmed.json()["data"]["task_id"]
                            confirm_task = await session.scalar(
                                select(ImportTaskRequest).where(
                                    ImportTaskRequest.task_token == UUID(confirm_task_id)
                                )
                            )
                            assert confirm_task is not None
                            assert confirm_task.state is ImportTaskState.REQUESTED
                            assert confirm_task.dispatch_attempts == 1
                            assert confirm_task.last_dispatch_attempt_at is not None
                            assert confirm_task.next_retry_at is not None
                            repeated = await client.post(
                                f"/api/v1/import-jobs/{job_id}/confirm",
                                json={"preview_revision": 1},
                            )
                            assert repeated.status_code == 200
                            assert repeated.json()["data"]["idempotent"] is True
                            assert (
                                repeated.json()["data"]["task_id"]
                                == dispatcher.confirm_calls[-1][2]
                            )
                            assert len(dispatcher.confirm_calls) == 1

                            await complete_task(session, confirm_task_id)
                            job.status = ImportJobStatus.COMPLETED
                            await session.commit()
                            completed_replay = await client.post(
                                f"/api/v1/import-jobs/{job_id}/confirm",
                                json={"preview_revision": 1},
                            )
                            assert completed_replay.status_code == 200
                            completed_data = completed_replay.json()["data"]
                            assert completed_data["idempotent"] is True
                            assert completed_data["task_id"] == confirm_task_id
                            assert len(dispatcher.confirm_calls) == 1

                            dispatcher.fail_parse = True
                            dispatch_failed = await client.post(
                                "/api/v1/import-jobs",
                                data={"collection_job_id": collection_id},
                                files={"file": ("dispatch-fail.csv", valid_csv(), "text/csv")},
                            )
                            assert dispatch_failed.status_code == 202
                            failed_job = await session.get(ImportJob, dispatcher.parse_calls[-1][0])
                            assert failed_job is not None
                            assert failed_job.status is ImportJobStatus.UPLOADED
                            assert failed_job.parse_task_id is not None
                            durable = await session.scalar(
                                select(ImportTaskRequest).where(
                                    ImportTaskRequest.task_token == UUID(failed_job.parse_task_id)
                                )
                            )
                            assert durable is not None
                            assert durable.state is ImportTaskState.REQUESTED
                            assert durable.dispatch_attempts == 1
                            dispatcher.fail_parse = False

                            async def viewer_auth() -> AuthContext:
                                return viewer_context

                            app.dependency_overrides[require_auth] = viewer_auth
                            app.dependency_overrides[require_csrf_context] = viewer_auth
                            client.cookies.set(
                                get_settings().csrf_cookie_name,
                                "csrf-HTTP Viewer",
                            )
                            client.headers["X-CSRF-Token"] = "csrf-HTTP Viewer"
                            viewer_create = await client.post(
                                "/api/v1/collection-jobs",
                                json={
                                    "name": "denied",
                                    "industry": "denied",
                                    "purpose": "denied",
                                    "target_action": "denied",
                                    "target_count": 1,
                                },
                            )
                            assert viewer_create.status_code == 403
                            assert viewer_create.json()["error"]["code"] == "PERMISSION_DENIED"
                            viewer_cross_read = await client.get(f"/api/v1/import-jobs/{job_id}")
                            assert viewer_cross_read.status_code == 403

                            async def no_operator_auth() -> AuthContext:
                                return no_operator_context

                            app.dependency_overrides[require_auth] = no_operator_auth
                            app.dependency_overrides[require_csrf_context] = no_operator_auth
                            client.cookies.set(
                                get_settings().csrf_cookie_name,
                                "csrf-HTTP No Operator",
                            )
                            client.headers["X-CSRF-Token"] = "csrf-HTTP No Operator"
                            missing_operator = await client.post(
                                "/api/v1/collection-jobs",
                                json={
                                    "name": "denied",
                                    "industry": "denied",
                                    "purpose": "denied",
                                    "target_action": "denied",
                                    "target_count": 1,
                                },
                            )
                            assert missing_operator.status_code == 409
                            assert missing_operator.json()["error"]["code"] == "OPERATOR_REQUIRED"

                            async def admin_auth() -> AuthContext:
                                return admin_context

                            app.dependency_overrides[require_auth] = admin_auth
                            app.dependency_overrides[require_csrf_context] = admin_auth
                            client.cookies.set(
                                get_settings().csrf_cookie_name,
                                "csrf-HTTP Admin",
                            )
                            client.headers["X-CSRF-Token"] = "csrf-HTTP Admin"
                            admin_cross_read = await client.get(f"/api/v1/import-jobs/{job_id}")
                            assert admin_cross_read.status_code == 200
                finally:
                    app.dependency_overrides.clear()

        await fake_redis.aclose()
        await engine.dispose()

    asyncio.run(scenario())


def test_import_mutation_requires_authentication_and_csrf() -> None:
    async def scenario() -> None:
        settings = get_settings()
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        fake_redis = FakeRedis(decode_responses=True)
        with TemporaryDirectory() as directory:
            storage = LocalStorageAdapter(Path(directory))
            async with factory() as session:
                context = await seed_context(
                    session, department_name="HTTP CSRF", role=Role.OPERATOR
                )

                async def override_database_session() -> AsyncIterator[AsyncSession]:
                    yield session

                def override_redis() -> FakeRedis:
                    return fake_redis

                def override_storage() -> LocalStorageAdapter:
                    return storage

                app.dependency_overrides[get_database_session] = override_database_session
                app.dependency_overrides[get_redis] = override_redis
                app.dependency_overrides[get_import_storage] = override_storage
                try:
                    async with app.router.lifespan_context(app):
                        transport = ASGITransport(app=app)
                        async with AsyncClient(
                            transport=transport, base_url="http://test"
                        ) as client:
                            unauthenticated = await client.post("/api/v1/collection-jobs", json={})
                            assert unauthenticated.status_code == 401
                            assert unauthenticated.json()["error"]["code"] == "AUTH_REQUIRED"

                            client.cookies.set(
                                settings.session_cookie_name,
                                "session-HTTP CSRF",
                            )
                            client.cookies.set(settings.csrf_cookie_name, "csrf-HTTP CSRF")
                            missing_header = await client.post("/api/v1/collection-jobs", json={})
                            assert missing_header.status_code == 403
                            assert missing_header.json()["error"]["code"] == "CSRF_FAILED"

                            valid_csrf_reaches_validation = await client.post(
                                "/api/v1/collection-jobs",
                                json={},
                                headers={"X-CSRF-Token": "csrf-HTTP CSRF"},
                            )
                            assert valid_csrf_reaches_validation.status_code == 422
                            assert context.operator is not None
                finally:
                    app.dependency_overrides.clear()

        await fake_redis.aclose()
        await engine.dispose()

    asyncio.run(scenario())
