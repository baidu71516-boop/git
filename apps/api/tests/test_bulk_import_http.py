import asyncio
import io
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
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
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.auth.security import hash_token
from backend_core.auth.service import AuthContext
from backend_core.config import get_settings
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.enums import ImportJobFileStatus, ImportJobStatus
from backend_core.imports.models import ImportJob, ImportJobFile, StoredImportFile
from backend_core.imports.storage import LocalStorageAdapter
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient, Response
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def valid_csv(label: str = "fixture") -> bytes:
    return (
        "达人名称,达人官方地址\n" f"{label},https://www.xiaohongshu.com/user/profile/{label}\n"
    ).encode("utf-8-sig")


def valid_xlsx(label: str = "xlsx-fixture") -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.append(["达人名称", "达人官方地址"])
    worksheet.append([label, f"https://www.xiaohongshu.com/user/profile/{label}"])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


async def seed_context(
    session: AsyncSession,
    *,
    department_name: str,
    role: Role,
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
        role=Role.OPERATOR,
        status=OperatorStatus.ACTIVE,
    )
    session.add(operator)
    await session.flush()
    auth_session = AuthSession(
        department_id=department.id,
        operator_id=operator.id,
        token_hash=hash_token(f"session-{department_name}"),
        csrf_token_hash=hash_token(f"csrf-{department_name}"),
        ip="127.0.0.1",
        user_agent="bulk-http-test",
        expires_at=datetime.now(UTC) + timedelta(hours=12),
    )
    session.add_all(
        [
            DepartmentPermission(department_id=department.id, role=role),
            auth_session,
        ]
    )
    await session.commit()
    return AuthContext(
        department=department,
        operator=operator,
        role=role,
        auth_session=auth_session,
    )


@dataclass
class BulkHttpHarness:
    client: AsyncClient
    session: AsyncSession
    storage: LocalStorageAdapter
    contexts: dict[str, AuthContext]
    current: dict[str, AuthContext]
    dispatcher: "NoopDispatcher"


class NoopDispatcher:
    def __init__(self) -> None:
        self.parse_calls: list[tuple[UUID, str]] = []
        self.parse_file_calls: list[tuple[UUID, UUID, str]] = []
        self.file_dispatch_had_open_transaction: list[bool] = []
        self.fail_parse_file = False
        self.session: AsyncSession | None = None

    async def parse(self, import_job_id: UUID, task_id: str) -> None:
        self.parse_calls.append((import_job_id, task_id))

    async def parse_file(
        self,
        import_job_id: UUID,
        import_job_file_id: UUID,
        task_id: str,
    ) -> None:
        self.parse_file_calls.append((import_job_id, import_job_file_id, task_id))
        if self.session is not None:
            self.file_dispatch_had_open_transaction.append(self.session.in_transaction())
        if self.fail_parse_file:
            raise RuntimeError("synthetic file broker failure")

    async def confirm(self, import_job_id: UUID, preview_revision: int, task_id: str) -> None:
        _ = (import_job_id, preview_revision, task_id)


@asynccontextmanager
async def bulk_http_harness() -> AsyncIterator[BulkHttpHarness]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    fake_redis = FakeRedis(decode_responses=True)
    with TemporaryDirectory() as directory:
        storage = LocalStorageAdapter(Path(directory))
        dispatcher = NoopDispatcher()
        async with factory() as session:
            dispatcher.session = session
            operator_context = await seed_context(
                session,
                department_name="Bulk HTTP Operator",
                role=Role.OPERATOR,
            )
            cross_department_context = await seed_context(
                session,
                department_name="Bulk HTTP Other",
                role=Role.OPERATOR,
            )
            super_admin_context = await seed_context(
                session,
                department_name="Bulk HTTP Admin",
                role=Role.SUPER_ADMIN,
            )
            current = {"auth": operator_context}

            async def override_database_session() -> AsyncIterator[AsyncSession]:
                try:
                    yield session
                finally:
                    if session.in_transaction():
                        await session.rollback()

            def override_redis() -> FakeRedis:
                return fake_redis

            def override_storage() -> LocalStorageAdapter:
                return storage

            def override_dispatcher() -> NoopDispatcher:
                return dispatcher

            async def override_auth() -> AuthContext:
                context = current["auth"]
                await session.refresh(context.department)
                if context.operator is not None:
                    await session.refresh(context.operator)
                await session.refresh(context.auth_session)
                return context

            app.dependency_overrides[get_database_session] = override_database_session
            app.dependency_overrides[get_redis] = override_redis
            app.dependency_overrides[get_import_storage] = override_storage
            app.dependency_overrides[get_import_task_dispatcher] = override_dispatcher
            app.dependency_overrides[require_auth] = override_auth
            app.dependency_overrides[require_csrf_context] = override_auth
            try:
                async with app.router.lifespan_context(app):
                    transport = ASGITransport(app=app)
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        yield BulkHttpHarness(
                            client=client,
                            session=session,
                            storage=storage,
                            contexts={
                                "operator": operator_context,
                                "manager": replace(operator_context, role=Role.MANAGER),
                                "viewer": replace(operator_context, role=Role.VIEWER),
                                "no_operator": replace(operator_context, operator=None),
                                "cross_department": cross_department_context,
                                "super_admin": super_admin_context,
                            },
                            current=current,
                            dispatcher=dispatcher,
                        )
            finally:
                app.dependency_overrides.clear()
    await fake_redis.aclose()
    await engine.dispose()


def set_context(harness: BulkHttpHarness, name: str) -> None:
    harness.current["auth"] = harness.contexts[name]


async def create_collection(client: AsyncClient, *, name: str = "Bulk HTTP Collection") -> UUID:
    response = await client.post(
        "/api/v1/collection-jobs",
        json={
            "name": name,
            "industry": "测试",
            "purpose": "验证 Multi-file HTTP contract",
            "target_action": "Preview",
            "target_count": 20,
            "source_type": "manual_huitun_export",
        },
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["data"]["id"])


async def create_bulk(client: AsyncClient, collection_job_id: UUID) -> Response:
    return await client.post(
        "/api/v1/import-jobs/bulk",
        json={"collection_job_id": str(collection_job_id)},
    )


async def upload_file(
    client: AsyncClient,
    import_job_id: UUID,
    *,
    client_file_id: str,
    filename: str,
    content: bytes,
    content_type: str,
    source_acquired_at: str | None = None,
) -> Response:
    data: dict[str, str] = {"client_file_id": client_file_id}
    if source_acquired_at is not None:
        data["source_acquired_at"] = source_acquired_at
    return await client.post(
        f"/api/v1/import-jobs/{import_job_id}/files",
        data=data,
        files={"file": (filename, content, content_type)},
    )


def upload_result(response: Response) -> tuple[dict[str, Any], bool]:
    data = response.json()["data"]
    assert isinstance(data, dict)
    file_data = data["file"]
    assert isinstance(file_data, dict)
    return file_data, bool(data["idempotent"])


def assert_no_storage_key(value: object) -> None:
    if isinstance(value, dict):
        assert "storage_key" not in value
        for child in value.values():
            assert_no_storage_key(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_storage_key(child)


def assert_success_envelope(response: Response) -> dict[str, Any]:
    body = cast(dict[str, Any], response.json())
    assert set(body) == {"success", "data", "error", "request_id"}
    assert body["success"] is True
    assert body["error"] is None
    assert isinstance(body["request_id"], str) and body["request_id"]
    assert response.headers["X-Request-ID"] == body["request_id"]
    return body


def assert_error_envelope(
    response: Response,
    *,
    status_code: int,
    code: str,
) -> dict[str, Any]:
    assert response.status_code == status_code, response.text
    body = cast(dict[str, Any], response.json())
    assert set(body) == {"success", "data", "error", "request_id"}
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == code
    assert isinstance(body["request_id"], str) and body["request_id"]
    assert response.headers["X-Request-ID"] == body["request_id"]
    return body


def test_bulk_import_http_multi_file_idempotency_acquisition_and_exclude() -> None:
    async def scenario() -> None:
        async with bulk_http_harness() as harness:
            collection_id = await create_collection(harness.client)
            created = await create_bulk(harness.client, collection_id)
            assert created.status_code == 201, created.text
            assert_success_envelope(created)
            assert created.json()["data"]["status"] == "draft"
            assert created.json()["data"]["stored_file_id"] is None
            assert created.json()["data"]["original_filename"] is None
            assert created.json()["data"]["sha256"] is None
            assert_no_storage_key(created.json())
            import_job_id = UUID(created.json()["data"]["id"])

            refresh_queue_is_not_a_task_2_field = await harness.client.post(
                "/api/v1/import-jobs/bulk",
                json={
                    "collection_job_id": str(collection_id),
                    "refresh_queue_id": str(collection_id),
                },
            )
            assert refresh_queue_is_not_a_task_2_field.status_code == 422

            first_content = valid_csv("first")
            first = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="client-a",
                filename="first.csv",
                content=first_content,
                content_type="text/csv",
            )
            assert first.status_code == 201, first.text
            first_file, first_idempotent = upload_result(first)
            assert first_idempotent is False
            assert first_file["position"] == 1
            assert first_file["status"] == "parsing"
            assert first_file["parse_task_id"] is not None
            assert first_file["source_acquired_at"] is not None
            assert first_file["source_acquired_at_origin"] == "server_default"
            assert first_file["source_acquired_at_confirmation_required"] is False
            assert_no_storage_key(first.json())
            first_file_id = UUID(first_file["id"])
            original_acquisition = first_file["source_acquired_at"]
            assert len(harness.dispatcher.parse_file_calls) == 1
            assert harness.dispatcher.parse_file_calls[0] == (
                import_job_id,
                first_file_id,
                first_file["parse_task_id"],
            )
            assert harness.dispatcher.file_dispatch_had_open_transaction == [False]

            replay = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="client-a",
                filename="renamed.csv",
                content=first_content,
                content_type="text/csv",
                source_acquired_at="2026-08-01T10:00:00+08:00",
            )
            assert replay.status_code == 200, replay.text
            replay_file, replay_idempotent = upload_result(replay)
            assert replay_idempotent is True
            assert UUID(replay_file["id"]) == first_file_id
            assert replay_file["source_acquired_at"] == original_acquisition
            assert replay_file["source_acquired_at_confirmation_required"] is False
            assert len(harness.dispatcher.parse_file_calls) == 1

            sha_alias = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="client-b",
                filename="same-content.csv",
                content=first_content,
                content_type="text/csv",
            )
            assert sha_alias.status_code == 200, sha_alias.text
            alias_file, alias_idempotent = upload_result(sha_alias)
            assert alias_idempotent is True
            assert UUID(alias_file["id"]) == first_file_id
            assert len(harness.dispatcher.parse_file_calls) == 1

            alias_conflict = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="client-b",
                filename="different.csv",
                content=valid_csv("different-for-b"),
                content_type="text/csv",
            )
            assert alias_conflict.status_code == 409, alias_conflict.text
            assert alias_conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

            second = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="client-c",
                filename="second.csv",
                content=valid_csv("second"),
                content_type="text/csv",
                source_acquired_at="2026-08-10T10:00:00+08:00",
            )
            assert second.status_code == 201, second.text
            second_file, _ = upload_result(second)
            assert second_file["position"] == 2
            assert second_file["source_acquired_at_origin"] == "user_confirmed"
            assert second_file["source_acquired_at_confirmation_required"] is False

            third = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="client-d",
                filename="third.xlsx",
                content=valid_xlsx(),
                content_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            )
            assert third.status_code == 201, third.text
            third_file, _ = upload_result(third)
            assert third_file["position"] == 3

            fourth = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="client-e",
                filename="../../fourth.csv",
                content=valid_csv("fourth"),
                content_type="text/csv",
            )
            assert fourth.status_code == 201, fourth.text
            fourth_file, _ = upload_result(fourth)
            assert fourth_file["position"] == 4
            assert fourth_file["original_filename"] == "fourth.csv"
            assert "/" not in fourth_file["original_filename"]

            listed = await harness.client.get(f"/api/v1/import-jobs/{import_job_id}/files")
            assert listed.status_code == 200, listed.text
            assert [item["position"] for item in listed.json()["data"]] == [1, 2, 3, 4]
            assert_no_storage_key(listed.json())

            patched = await harness.client.patch(
                f"/api/v1/import-jobs/{import_job_id}/files/{first_file_id}",
                json={"source_acquired_at": "2026-08-09T09:30:00+08:00"},
            )
            assert patched.status_code == 200, patched.text
            assert patched.json()["data"]["source_acquired_at_origin"] == "user_confirmed"
            assert patched.json()["data"]["source_acquired_at"] != original_acquisition
            assert_no_storage_key(patched.json())

            naive_time = await harness.client.patch(
                f"/api/v1/import-jobs/{import_job_id}/files/{first_file_id}",
                json={"source_acquired_at": "2026-08-09T09:30:00"},
            )
            assert naive_time.status_code == 422, naive_time.text

            future_time = await harness.client.patch(
                f"/api/v1/import-jobs/{import_job_id}/files/{first_file_id}",
                json={
                    "source_acquired_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
                },
            )
            assert future_time.status_code == 422, future_time.text

            fourth_occurrence = await harness.session.get(
                ImportJobFile,
                UUID(fourth_file["id"]),
            )
            assert fourth_occurrence is not None
            fourth_occurrence.status = ImportJobFileStatus.READY
            await harness.session.commit()
            excluded = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/files/{fourth_file['id']}/exclude"
            )
            assert excluded.status_code == 200, excluded.text
            assert excluded.json()["data"]["status"] == "excluded"
            assert excluded.json()["data"]["excluded_at"] is not None
            assert_no_storage_key(excluded.json())

            occurrences = (
                await harness.session.scalars(
                    select(ImportJobFile)
                    .where(ImportJobFile.import_job_id == import_job_id)
                    .order_by(ImportJobFile.position)
                )
            ).all()
            assert len(occurrences) == 4
            assert [occurrence.position for occurrence in occurrences] == [1, 2, 3, 4]

            second_bulk = await create_bulk(harness.client, collection_id)
            assert second_bulk.status_code == 201, second_bulk.text
            second_job_id = UUID(second_bulk.json()["data"]["id"])
            historical_reuse = await upload_file(
                harness.client,
                second_job_id,
                client_file_id="client-a",
                filename="historical.csv",
                content=first_content,
                content_type="text/csv",
            )
            assert historical_reuse.status_code == 201, historical_reuse.text
            historical_file, historical_idempotent = upload_result(historical_reuse)
            assert historical_idempotent is False
            assert UUID(historical_file["id"]) != first_file_id
            assert historical_file["position"] == 1
            assert historical_file["source_acquired_at_confirmation_required"] is True

            confirmed_history = await harness.client.patch(
                f"/api/v1/import-jobs/{second_job_id}/files/{historical_file['id']}",
                json={"source_acquired_at": historical_file["source_acquired_at"]},
            )
            assert confirmed_history.status_code == 200, confirmed_history.text
            assert (
                confirmed_history.json()["data"]["source_acquired_at_confirmation_required"]
                is False
            )
            assert confirmed_history.json()["data"]["source_acquired_at_origin"] == "user_confirmed"

            stored_count = await harness.session.scalar(
                select(func.count()).select_from(StoredImportFile)
            )
            assert stored_count == 4

    asyncio.run(scenario())


def test_bulk_file_mapping_retry_and_dispatch_failure_are_file_scoped() -> None:
    async def scenario() -> None:
        async with bulk_http_harness() as harness:
            collection_id = await create_collection(harness.client, name="Bulk File Parse")
            bulk = await create_bulk(harness.client, collection_id)
            assert bulk.status_code == 201, bulk.text
            import_job_id = UUID(bulk.json()["data"]["id"])

            uploaded = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="mapping-file",
                filename="mapping.csv",
                content=valid_csv("mapping"),
                content_type="text/csv",
            )
            assert uploaded.status_code == 201, uploaded.text
            uploaded_file, _ = upload_result(uploaded)
            import_job_file_id = UUID(uploaded_file["id"])
            assert uploaded_file["status"] == "parsing"
            assert harness.dispatcher.parse_file_calls[-1][:2] == (
                import_job_id,
                import_job_file_id,
            )

            occurrence = await harness.session.get(ImportJobFile, import_job_file_id)
            assert occurrence is not None
            occurrence.status = ImportJobFileStatus.MAPPING_REQUIRED
            occurrence.detected_fields = ["达人名称", "达人官方地址"]
            await harness.session.commit()

            mapped = await harness.client.put(
                f"/api/v1/import-jobs/{import_job_id}/files/{import_job_file_id}/mapping",
                json={
                    "mapping": {
                        "达人名称": "nickname",
                        "达人官方地址": "profile_url",
                    }
                },
            )
            assert mapped.status_code == 202, mapped.text
            assert mapped.json()["data"]["status"] == "parsing"
            assert mapped.json()["data"]["field_mapping"] == {
                "达人名称": "nickname",
                "达人官方地址": "profile_url",
            }
            assert len(harness.dispatcher.parse_file_calls) == 2
            assert harness.dispatcher.parse_file_calls[-1][:2] == (
                import_job_id,
                import_job_file_id,
            )

            await harness.session.refresh(occurrence)
            occurrence.status = ImportJobFileStatus.FAILED
            occurrence.error_code = "INVALID_CSV"
            occurrence.error_message = "synthetic deterministic failure"
            await harness.session.commit()
            retried = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/files/{import_job_file_id}/retry"
            )
            assert retried.status_code == 202, retried.text
            assert retried.json()["data"]["status"] == "parsing"
            assert retried.json()["data"]["error_code"] is None
            assert len(harness.dispatcher.parse_file_calls) == 3
            assert harness.dispatcher.parse_file_calls[-1][:2] == (
                import_job_id,
                import_job_file_id,
            )

            failed_bulk = await create_bulk(harness.client, collection_id)
            assert failed_bulk.status_code == 201, failed_bulk.text
            failed_job_id = UUID(failed_bulk.json()["data"]["id"])
            harness.dispatcher.fail_parse_file = True
            dispatch_failed = await upload_file(
                harness.client,
                failed_job_id,
                client_file_id="dispatch-failure",
                filename="dispatch-failure.csv",
                content=valid_csv("dispatch-failure"),
                content_type="text/csv",
            )
            assert_error_envelope(
                dispatch_failed,
                status_code=503,
                code="TASK_DISPATCH_FAILED",
            )
            harness.dispatcher.fail_parse_file = False

            failed_job = await harness.session.get(ImportJob, failed_job_id)
            assert failed_job is not None
            assert failed_job.status is ImportJobStatus.DRAFT
            failed_occurrences = (
                await harness.session.scalars(
                    select(ImportJobFile).where(ImportJobFile.import_job_id == failed_job_id)
                )
            ).all()
            assert len(failed_occurrences) == 1
            assert failed_occurrences[0].status is ImportJobFileStatus.FAILED
            assert failed_occurrences[0].error_code == "TASK_DISPATCH_FAILED"

    asyncio.run(scenario())


def test_bulk_import_http_rbac_operator_and_department_scope() -> None:
    async def scenario() -> None:
        async with bulk_http_harness() as harness:
            collection_id = await create_collection(harness.client, name="Bulk RBAC")
            bulk = await create_bulk(harness.client, collection_id)
            assert bulk.status_code == 201, bulk.text
            import_job_id = UUID(bulk.json()["data"]["id"])
            other_bulk = await create_bulk(harness.client, collection_id)
            assert other_bulk.status_code == 201, other_bulk.text
            other_job_id = UUID(other_bulk.json()["data"]["id"])

            set_context(harness, "viewer")
            viewer_create = await create_bulk(harness.client, collection_id)
            assert viewer_create.status_code == 403
            viewer_upload = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="viewer-file",
                filename="viewer.csv",
                content=valid_csv("viewer"),
                content_type="text/csv",
            )
            assert viewer_upload.status_code == 403
            viewer_read = await harness.client.get(f"/api/v1/import-jobs/{import_job_id}/files")
            assert viewer_read.status_code == 200

            set_context(harness, "manager")
            manager_upload = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="manager-file",
                filename="manager.csv",
                content=valid_csv("manager"),
                content_type="text/csv",
            )
            assert manager_upload.status_code == 201
            manager_file, _ = upload_result(manager_upload)
            manager_file_id = UUID(manager_file["id"])

            manager_create = await create_bulk(harness.client, collection_id)
            assert manager_create.status_code == 201

            set_context(harness, "viewer")
            viewer_patch = await harness.client.patch(
                f"/api/v1/import-jobs/{import_job_id}/files/{manager_file_id}",
                json={"source_acquired_at": "2026-08-10T10:00:00+08:00"},
            )
            assert_error_envelope(viewer_patch, status_code=403, code="PERMISSION_DENIED")
            viewer_exclude = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/files/{manager_file_id}/exclude"
            )
            assert_error_envelope(viewer_exclude, status_code=403, code="PERMISSION_DENIED")
            viewer_mapping = await harness.client.put(
                f"/api/v1/import-jobs/{import_job_id}/files/{manager_file_id}/mapping",
                json={"mapping": {"达人名称": "nickname"}},
            )
            assert_error_envelope(viewer_mapping, status_code=403, code="PERMISSION_DENIED")
            viewer_retry = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/files/{manager_file_id}/retry"
            )
            assert_error_envelope(viewer_retry, status_code=403, code="PERMISSION_DENIED")

            set_context(harness, "no_operator")
            no_operator_create = await create_bulk(harness.client, collection_id)
            assert no_operator_create.status_code == 409
            assert no_operator_create.json()["error"]["code"] == "OPERATOR_REQUIRED"
            no_operator_upload = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="no-operator-file",
                filename="no-operator.csv",
                content=valid_csv("no-operator"),
                content_type="text/csv",
            )
            assert no_operator_upload.status_code == 409
            assert no_operator_upload.json()["error"]["code"] == "OPERATOR_REQUIRED"
            no_operator_mapping = await harness.client.put(
                f"/api/v1/import-jobs/{import_job_id}/files/{manager_file_id}/mapping",
                json={"mapping": {"达人名称": "nickname"}},
            )
            assert_error_envelope(
                no_operator_mapping,
                status_code=409,
                code="OPERATOR_REQUIRED",
            )
            no_operator_retry = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/files/{manager_file_id}/retry"
            )
            assert_error_envelope(
                no_operator_retry,
                status_code=409,
                code="OPERATOR_REQUIRED",
            )
            no_operator_read = await harness.client.get(
                f"/api/v1/import-jobs/{import_job_id}/files"
            )
            assert no_operator_read.status_code == 200

            set_context(harness, "cross_department")
            cross_read = await harness.client.get(f"/api/v1/import-jobs/{import_job_id}/files")
            assert_error_envelope(cross_read, status_code=404, code="IMPORT_JOB_NOT_FOUND")
            cross_upload = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="cross-file",
                filename="cross.csv",
                content=valid_csv("cross"),
                content_type="text/csv",
            )
            assert_error_envelope(cross_upload, status_code=404, code="IMPORT_JOB_NOT_FOUND")
            cross_patch = await harness.client.patch(
                f"/api/v1/import-jobs/{import_job_id}/files/{manager_file_id}",
                json={"source_acquired_at": "2026-08-10T10:00:00+08:00"},
            )
            assert_error_envelope(cross_patch, status_code=404, code="IMPORT_JOB_NOT_FOUND")
            cross_exclude = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/files/{manager_file_id}/exclude"
            )
            assert_error_envelope(cross_exclude, status_code=404, code="IMPORT_JOB_NOT_FOUND")
            cross_mapping = await harness.client.put(
                f"/api/v1/import-jobs/{import_job_id}/files/{manager_file_id}/mapping",
                json={"mapping": {"达人名称": "nickname"}},
            )
            assert_error_envelope(
                cross_mapping,
                status_code=404,
                code="IMPORT_JOB_NOT_FOUND",
            )
            cross_retry = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/files/{manager_file_id}/retry"
            )
            assert_error_envelope(
                cross_retry,
                status_code=404,
                code="IMPORT_JOB_NOT_FOUND",
            )

            set_context(harness, "manager")
            wrong_parent_patch = await harness.client.patch(
                f"/api/v1/import-jobs/{other_job_id}/files/{manager_file_id}",
                json={"source_acquired_at": "2026-08-10T10:00:00+08:00"},
            )
            assert_error_envelope(
                wrong_parent_patch,
                status_code=404,
                code="IMPORT_JOB_FILE_NOT_FOUND",
            )
            wrong_parent_exclude = await harness.client.post(
                f"/api/v1/import-jobs/{other_job_id}/files/{manager_file_id}/exclude"
            )
            assert_error_envelope(
                wrong_parent_exclude,
                status_code=404,
                code="IMPORT_JOB_FILE_NOT_FOUND",
            )
            wrong_parent_mapping = await harness.client.put(
                f"/api/v1/import-jobs/{other_job_id}/files/{manager_file_id}/mapping",
                json={"mapping": {"达人名称": "nickname"}},
            )
            assert_error_envelope(
                wrong_parent_mapping,
                status_code=404,
                code="IMPORT_JOB_FILE_NOT_FOUND",
            )
            wrong_parent_retry = await harness.client.post(
                f"/api/v1/import-jobs/{other_job_id}/files/{manager_file_id}/retry"
            )
            assert_error_envelope(
                wrong_parent_retry,
                status_code=404,
                code="IMPORT_JOB_FILE_NOT_FOUND",
            )

            manager_occurrence = await harness.session.get(ImportJobFile, manager_file_id)
            assert manager_occurrence is not None
            manager_occurrence.status = ImportJobFileStatus.READY
            await harness.session.commit()
            set_context(harness, "super_admin")
            admin_read = await harness.client.get(f"/api/v1/import-jobs/{import_job_id}/files")
            assert admin_read.status_code == 200
            admin_patch = await harness.client.patch(
                f"/api/v1/import-jobs/{import_job_id}/files/{manager_file_id}",
                json={"source_acquired_at": "2026-08-10T10:00:00+08:00"},
            )
            assert admin_patch.status_code == 200, admin_patch.text
            admin_exclude = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/files/{manager_file_id}/exclude"
            )
            assert admin_exclude.status_code == 200, admin_exclude.text

    asyncio.run(scenario())


def test_bulk_import_mutation_requires_authentication_and_csrf() -> None:
    async def scenario() -> None:
        settings = get_settings()
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        fake_redis = FakeRedis(decode_responses=True)
        with TemporaryDirectory() as directory:
            storage = LocalStorageAdapter(Path(directory))
            dispatcher = NoopDispatcher()
            async with factory() as session:
                context = await seed_context(
                    session,
                    department_name="Bulk HTTP CSRF",
                    role=Role.OPERATOR,
                )

                async def override_database_session() -> AsyncIterator[AsyncSession]:
                    yield session

                def override_redis() -> FakeRedis:
                    return fake_redis

                def override_storage() -> LocalStorageAdapter:
                    return storage

                def override_dispatcher() -> NoopDispatcher:
                    return dispatcher

                app.dependency_overrides[get_database_session] = override_database_session
                app.dependency_overrides[get_redis] = override_redis
                app.dependency_overrides[get_import_storage] = override_storage
                app.dependency_overrides[get_import_task_dispatcher] = override_dispatcher
                try:
                    async with app.router.lifespan_context(app):
                        transport = ASGITransport(app=app)
                        async with AsyncClient(
                            transport=transport,
                            base_url="http://test",
                        ) as client:
                            unauthenticated = await client.post(
                                "/api/v1/import-jobs/bulk",
                                json={},
                            )
                            assert_error_envelope(
                                unauthenticated,
                                status_code=401,
                                code="AUTH_REQUIRED",
                            )
                            unauthenticated_read = await client.get(
                                f"/api/v1/import-jobs/{UUID(int=1)}/files"
                            )
                            assert_error_envelope(
                                unauthenticated_read,
                                status_code=401,
                                code="AUTH_REQUIRED",
                            )

                            client.cookies.set(
                                settings.session_cookie_name,
                                "session-Bulk HTTP CSRF",
                            )
                            client.cookies.set(
                                settings.csrf_cookie_name,
                                "csrf-Bulk HTTP CSRF",
                            )
                            missing_header = await client.post(
                                "/api/v1/import-jobs/bulk",
                                json={},
                            )
                            assert_error_envelope(
                                missing_header,
                                status_code=403,
                                code="CSRF_FAILED",
                            )

                            reaches_validation = await client.post(
                                "/api/v1/import-jobs/bulk",
                                json={},
                                headers={"X-CSRF-Token": "csrf-Bulk HTTP CSRF"},
                            )
                            assert_error_envelope(
                                reaches_validation,
                                status_code=422,
                                code="VALIDATION_ERROR",
                            )

                            csrf_headers = {"X-CSRF-Token": "csrf-Bulk HTTP CSRF"}
                            collection = await client.post(
                                "/api/v1/collection-jobs",
                                json={
                                    "name": "Bulk CSRF Collection",
                                    "industry": "测试",
                                    "purpose": "验证所有 Bulk Mutation CSRF",
                                    "target_action": "Preview",
                                    "target_count": 20,
                                },
                                headers=csrf_headers,
                            )
                            assert collection.status_code == 201, collection.text
                            collection_id = collection.json()["data"]["id"]
                            bulk = await client.post(
                                "/api/v1/import-jobs/bulk",
                                json={"collection_job_id": collection_id},
                                headers=csrf_headers,
                            )
                            assert bulk.status_code == 201, bulk.text
                            import_job_id = bulk.json()["data"]["id"]
                            uploaded = await client.post(
                                f"/api/v1/import-jobs/{import_job_id}/files",
                                data={"client_file_id": "csrf-fixture"},
                                files={"file": ("csrf.csv", valid_csv("csrf"), "text/csv")},
                                headers=csrf_headers,
                            )
                            assert uploaded.status_code == 201, uploaded.text
                            import_job_file_id = uploaded.json()["data"]["file"]["id"]

                            read_without_csrf = await client.get(
                                f"/api/v1/import-jobs/{import_job_id}/files"
                            )
                            assert read_without_csrf.status_code == 200

                            mutation_without_csrf = (
                                await client.post(
                                    "/api/v1/import-jobs/bulk",
                                    json={"collection_job_id": collection_id},
                                ),
                                await client.post(
                                    f"/api/v1/import-jobs/{import_job_id}/files",
                                    data={"client_file_id": "missing-csrf"},
                                    files={
                                        "file": (
                                            "missing-csrf.csv",
                                            valid_csv("missing-csrf"),
                                            "text/csv",
                                        )
                                    },
                                ),
                                await client.patch(
                                    f"/api/v1/import-jobs/{import_job_id}/files/"
                                    f"{import_job_file_id}",
                                    json={"source_acquired_at": "2026-08-10T10:00:00+08:00"},
                                ),
                                await client.put(
                                    f"/api/v1/import-jobs/{import_job_id}/files/"
                                    f"{import_job_file_id}/mapping",
                                    json={
                                        "mapping": {
                                            "达人名称": "nickname",
                                            "达人官方地址": "profile_url",
                                        }
                                    },
                                ),
                                await client.post(
                                    f"/api/v1/import-jobs/{import_job_id}/files/"
                                    f"{import_job_file_id}/retry"
                                ),
                                await client.post(
                                    f"/api/v1/import-jobs/{import_job_id}/files/"
                                    f"{import_job_file_id}/exclude"
                                ),
                            )
                            for rejected in mutation_without_csrf:
                                assert_error_envelope(
                                    rejected,
                                    status_code=403,
                                    code="CSRF_FAILED",
                                )
                            assert context.operator is not None
                finally:
                    app.dependency_overrides.clear()
        await fake_redis.aclose()
        await engine.dispose()

    asyncio.run(scenario())


def test_bulk_file_upload_rejects_invalid_media_and_oversized_file() -> None:
    async def scenario() -> None:
        async with bulk_http_harness() as harness:
            collection_id = await create_collection(harness.client, name="Bulk Validation")
            bulk = await create_bulk(harness.client, collection_id)
            assert bulk.status_code == 201, bulk.text
            import_job_id = UUID(bulk.json()["data"]["id"])

            invalid_extension = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="invalid-extension",
                filename="invalid.txt",
                content=b"not an import",
                content_type="text/plain",
            )
            assert_error_envelope(
                invalid_extension,
                status_code=415,
                code="INVALID_FILE_EXTENSION",
            )

            invalid_xlsx = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="invalid-xlsx",
                filename="invalid.xlsx",
                content=b"not an xlsx archive",
                content_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            )
            assert_error_envelope(
                invalid_xlsx,
                status_code=415,
                code="MIME_MISMATCH",
            )

            mime_mismatch = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="mime-mismatch",
                filename="mime-mismatch.csv",
                content=valid_csv("mime-mismatch"),
                content_type="application/pdf",
            )
            assert_error_envelope(
                mime_mismatch,
                status_code=415,
                code="MIME_MISMATCH",
            )

            invalid_csv = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="invalid-csv",
                filename="invalid.csv",
                content=b"header\x00,value\n",
                content_type="text/csv",
            )
            assert_error_envelope(
                invalid_csv,
                status_code=422,
                code="INVALID_CSV",
            )

            corrupt_supported_xlsx = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="corrupt-supported-xlsx",
                filename="corrupt.xlsx",
                content=b"PK\x03\x04not-a-valid-zip",
                content_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            )
            assert_error_envelope(
                corrupt_supported_xlsx,
                status_code=422,
                code="INVALID_XLSX",
            )

            empty_file = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="empty-file",
                filename="empty.csv",
                content=b"",
                content_type="text/csv",
            )
            assert_error_envelope(
                empty_file,
                status_code=422,
                code="EMPTY_FILE",
            )

            oversized = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="oversized",
                filename="oversized.csv",
                content=b"x" * (get_settings().import_max_file_bytes + 1),
                content_type="text/csv",
            )
            assert_error_envelope(
                oversized,
                status_code=413,
                code="FILE_TOO_LARGE",
            )

            listed = await harness.client.get(f"/api/v1/import-jobs/{import_job_id}/files")
            assert listed.status_code == 200
            assert listed.json()["data"] == []

    asyncio.run(scenario())


def _resolve_openapi_schema(
    document: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    while "$ref" in schema:
        reference = schema["$ref"]
        assert isinstance(reference, str)
        target: Any = document
        for part in reference.removeprefix("#/").split("/"):
            target = target[part]
        assert isinstance(target, dict)
        schema = target
    return schema


def _response_schema(
    document: dict[str, Any],
    operation: dict[str, Any],
    status_code: int,
) -> dict[str, Any]:
    schema = operation["responses"][str(status_code)]["content"]["application/json"]["schema"]
    assert isinstance(schema, dict)
    return _resolve_openapi_schema(document, schema)


def _schema_property_names(
    document: dict[str, Any],
    schema: dict[str, Any],
    *,
    visited: set[str] | None = None,
) -> set[str]:
    seen = visited if visited is not None else set()
    reference = schema.get("$ref")
    if isinstance(reference, str):
        if reference in seen:
            return set()
        seen.add(reference)
        schema = _resolve_openapi_schema(document, schema)
    names = set(schema.get("properties", {}))
    for value in schema.values():
        if isinstance(value, dict):
            names.update(_schema_property_names(document, value, visited=seen))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    names.update(_schema_property_names(document, item, visited=seen))
    return names


def test_bulk_openapi_has_typed_envelopes_for_file_endpoints() -> None:
    document = app.openapi()
    paths = document["paths"]
    bulk_create = paths["/api/v1/import-jobs/bulk"]["post"]
    upload = paths["/api/v1/import-jobs/{import_job_id}/files"]["post"]
    list_files = paths["/api/v1/import-jobs/{import_job_id}/files"]["get"]
    patch_file = paths["/api/v1/import-jobs/{import_job_id}/files/{import_job_file_id}"]["patch"]
    exclude_file = paths["/api/v1/import-jobs/{import_job_id}/files/{import_job_file_id}/exclude"][
        "post"
    ]
    mapping_file = paths["/api/v1/import-jobs/{import_job_id}/files/{import_job_file_id}/mapping"][
        "put"
    ]
    retry_file = paths["/api/v1/import-jobs/{import_job_id}/files/{import_job_file_id}/retry"][
        "post"
    ]

    bulk_envelope = _response_schema(document, bulk_create, 201)
    bulk_data = _resolve_openapi_schema(document, bulk_envelope["properties"]["data"])
    assert {"id", "collection_job_id", "status", "stored_file_id"} <= set(bulk_data["properties"])

    upload_201 = _response_schema(document, upload, 201)
    upload_200 = _response_schema(document, upload, 200)
    for upload_envelope in (upload_200, upload_201):
        assert {"success", "data", "error", "request_id"} <= set(upload_envelope["properties"])
        upload_data = _resolve_openapi_schema(document, upload_envelope["properties"]["data"])
        assert {"file", "idempotent"} <= set(upload_data["properties"])
        file_schema = _resolve_openapi_schema(document, upload_data["properties"]["file"])
        assert (
            file_schema["properties"]["source_acquired_at_confirmation_required"]["type"]
            == "boolean"
        )
        assert "source_acquired_at_confirmation_required" in file_schema["required"]

    list_envelope = _response_schema(document, list_files, 200)
    list_data = _resolve_openapi_schema(document, list_envelope["properties"]["data"])
    assert list_data["type"] == "array"
    list_item = _resolve_openapi_schema(document, list_data["items"])
    assert "source_acquired_at_confirmation_required" in list_item["properties"]

    for operation in (patch_file, exclude_file):
        file_envelope = _response_schema(document, operation, 200)
        file_data = _resolve_openapi_schema(document, file_envelope["properties"]["data"])
        assert "source_acquired_at_confirmation_required" in file_data["properties"]

    for operation in (mapping_file, retry_file):
        file_envelope = _response_schema(document, operation, 202)
        file_data = _resolve_openapi_schema(document, file_envelope["properties"]["data"])
        assert "parse_task_id" in file_data["properties"]
        assert "field_mapping" in file_data["properties"]
        assert {"200", "202", "401", "403", "404", "409", "422", "503"} <= set(
            operation["responses"]
        )

    validation_envelope = _response_schema(document, upload, 422)
    assert {"success", "data", "error", "request_id"} <= set(validation_envelope["properties"])
    error_body = _resolve_openapi_schema(document, validation_envelope["properties"]["error"])
    assert {"code", "message", "details"} <= set(error_body["properties"])

    assert {
        "200",
        "201",
        "401",
        "403",
        "404",
        "409",
        "413",
        "415",
        "422",
        "503",
    } <= set(upload["responses"])
    for operation, success_status in (
        (bulk_create, 201),
        (upload, 200),
        (upload, 201),
        (list_files, 200),
        (patch_file, 200),
        (exclude_file, 200),
        (mapping_file, 202),
        (retry_file, 202),
    ):
        assert "storage_key" not in _schema_property_names(
            document,
            operation["responses"][str(success_status)]["content"]["application/json"]["schema"],
        )


def test_legacy_import_job_http_response_contract_remains_compatible() -> None:
    legacy_fields = {
        "id",
        "collection_job_id",
        "department_id",
        "operator_id",
        "original_filename",
        "mime_type",
        "file_size",
        "sha256",
        "source_type",
        "status",
        "detected_fields",
        "field_mapping",
        "preview_revision",
        "preview_summary",
        "result",
        "total_rows",
        "valid_rows",
        "warning_rows",
        "error_rows",
        "created_rows",
        "updated_rows",
        "no_change_rows",
        "skipped_rows",
        "manual_review_rows",
        "confirmed_revision",
        "confirmed_at",
        "completed_at",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
    }

    async def scenario() -> None:
        async with bulk_http_harness() as harness:
            collection_id = await create_collection(harness.client, name="Legacy Contract")
            uploaded = await harness.client.post(
                "/api/v1/import-jobs",
                data={"collection_job_id": str(collection_id)},
                files={"file": ("legacy.csv", valid_csv("legacy"), "text/csv")},
            )
            assert uploaded.status_code == 202, uploaded.text
            upload_data = assert_success_envelope(uploaded)["data"]
            assert isinstance(upload_data, dict)
            assert legacy_fields <= set(upload_data)
            assert upload_data["original_filename"] == "legacy.csv"
            assert upload_data["mime_type"] == "text/csv"
            assert upload_data["file_size"] == len(valid_csv("legacy"))
            assert upload_data["sha256"] is not None
            assert upload_data["status"] == "uploaded"
            assert upload_data["stored_file_id"] is not None
            assert upload_data["mapping_hash"] is None
            assert_no_storage_key(uploaded.json())

            fetched = await harness.client.get(f"/api/v1/import-jobs/{upload_data['id']}")
            assert fetched.status_code == 200, fetched.text
            fetched_data = assert_success_envelope(fetched)["data"]
            assert isinstance(fetched_data, dict)
            assert {key: fetched_data[key] for key in legacy_fields} == {
                key: upload_data[key] for key in legacy_fields
            }
            assert_no_storage_key(fetched.json())

    asyncio.run(scenario())
