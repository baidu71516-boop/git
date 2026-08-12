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
from backend_core.audit.models import AuditLog
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator
from backend_core.auth.security import hash_token
from backend_core.auth.service import AuthContext
from backend_core.config import get_settings
from backend_core.db import models as database_models  # noqa: F401
from backend_core.db.base import Base
from backend_core.imports.enums import (
    ImportJobFailedStage,
    ImportJobFileStatus,
    ImportJobStatus,
    ImportMatchType,
    ImportRowAction,
)
from backend_core.imports.models import ImportJob, ImportJobFile, ImportRow, StoredImportFile
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
        self.preview_calls: list[tuple[UUID, str]] = []
        self.file_dispatch_had_open_transaction: list[bool] = []
        self.fail_parse_file = False
        self.fail_preview = False
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

    async def preview(self, import_job_id: UUID, task_id: str) -> None:
        self.preview_calls.append((import_job_id, task_id))
        if self.fail_preview:
            raise RuntimeError("synthetic Preview broker failure")


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


def test_bulk_preview_dispatch_is_idempotent_rows_are_categorized_and_confirm_is_gated() -> None:
    async def scenario() -> None:
        async with bulk_http_harness() as harness:
            collection_id = await create_collection(harness.client, name="Bulk Preview HTTP")
            created = await create_bulk(harness.client, collection_id)
            assert created.status_code == 201, created.text
            import_job_id = UUID(created.json()["data"]["id"])
            uploaded = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="preview-file",
                filename="preview.csv",
                content=valid_csv("preview"),
                content_type="text/csv",
                source_acquired_at="2026-08-10T10:00:00+08:00",
            )
            assert uploaded.status_code == 201, uploaded.text
            file_data, _ = upload_result(uploaded)
            occurrence = await harness.session.get(ImportJobFile, UUID(file_data["id"]))
            assert occurrence is not None
            occurrence.status = ImportJobFileStatus.READY
            occurrence.raw_rows = 4
            await harness.session.commit()

            first = await harness.client.post(f"/api/v1/import-jobs/{import_job_id}/preview")
            assert first.status_code == 202, first.text
            first_data = assert_success_envelope(first)["data"]
            assert first_data["status"] == "previewing"
            assert isinstance(first_data["task_id"], str) and first_data["task_id"]
            assert first_data["idempotent"] is False
            assert harness.dispatcher.preview_calls == [
                (import_job_id, first_data["task_id"]),
            ]

            replay = await harness.client.post(f"/api/v1/import-jobs/{import_job_id}/preview")
            assert replay.status_code == 200, replay.text
            replay_data = assert_success_envelope(replay)["data"]
            assert replay_data["task_id"] == first_data["task_id"]
            assert replay_data["idempotent"] is True
            assert harness.dispatcher.preview_calls == [
                (import_job_id, first_data["task_id"]),
            ]

            job = await harness.session.get(ImportJob, import_job_id)
            assert job is not None
            job.status = ImportJobStatus.PREVIEW_READY
            job.preview_revision = 1
            job.preview_summary = {"raw_rows": 4}
            harness.session.add_all(
                [
                    ImportRow(
                        import_job_id=job.id,
                        import_job_file_id=occurrence.id,
                        row_number=2,
                        raw_data={"达人名称": "new"},
                        normalized_data={"display_name": "new"},
                        match_type=ImportMatchType.NONE,
                        action=ImportRowAction.CREATE,
                        merge_plan={},
                        warnings=[],
                        errors=[],
                        preview_revision=1,
                        plan_hash="1" * 64,
                    ),
                    ImportRow(
                        import_job_id=job.id,
                        import_job_file_id=occurrence.id,
                        row_number=3,
                        raw_data={
                            "达人名称": "warning",
                            "邮箱": "viewer-secret@example.com",
                        },
                        normalized_data={
                            "display_name": "warning",
                            "public_profile": {
                                "bio": "商务联系：nested-secret@example.com",
                                "details": [
                                    {"phone_note": "联系电话：13800138000"},
                                    {"wechat_note": "wx13800138000"},
                                    {"observed_at": "2026-08-10T10:00:00+08:00"},
                                    {"score": "98.123456789"},
                                ],
                            },
                            "contacts": [
                                {
                                    "type": "email",
                                    "value": "viewer-secret@example.com",
                                    "normalized_value": "viewer-secret@example.com",
                                    "validation_status": "valid",
                                }
                            ],
                        },
                        match_type=ImportMatchType.NONE,
                        action=ImportRowAction.CREATE,
                        merge_plan={
                            "contacts": {
                                "create": [
                                    {
                                        "type": "email",
                                        "value": "viewer-secret@example.com",
                                        "normalized_value": "viewer-secret@example.com",
                                    }
                                ]
                            }
                        },
                        warnings=[{"code": "SYNTHETIC_WARNING", "message": "warning"}],
                        errors=[],
                        preview_revision=1,
                        plan_hash="2" * 64,
                    ),
                    ImportRow(
                        import_job_id=job.id,
                        import_job_file_id=occurrence.id,
                        row_number=4,
                        raw_data={"达人名称": "duplicate"},
                        normalized_data={"display_name": "duplicate"},
                        match_type=ImportMatchType.NONE,
                        action=ImportRowAction.SKIP,
                        merge_plan={"batch_duplicate": {"owner_row_number": 2}},
                        warnings=[{"code": "BATCH_DUPLICATE", "message": "duplicate"}],
                        errors=[],
                        preview_revision=1,
                        plan_hash="3" * 64,
                    ),
                    ImportRow(
                        import_job_id=job.id,
                        import_job_file_id=occurrence.id,
                        row_number=5,
                        raw_data={"达人名称": "error"},
                        normalized_data={"display_name": "error"},
                        match_type=ImportMatchType.NONE,
                        action=ImportRowAction.ERROR,
                        merge_plan={},
                        warnings=[],
                        errors=[{"code": "SYNTHETIC_ERROR", "message": "error"}],
                        preview_revision=1,
                        plan_hash="4" * 64,
                    ),
                ]
            )
            await harness.session.commit()

            for category, expected in (
                ("all", 4),
                ("new", 2),
                ("warning", 2),
                ("attention", 3),
                ("duplicate", 1),
                ("error", 1),
            ):
                response = await harness.client.get(
                    f"/api/v1/import-jobs/{import_job_id}/rows",
                    params={"category": category, "offset": 0, "limit": 50},
                )
                assert response.status_code == 200, response.text
                page = assert_success_envelope(response)["data"]
                assert page["total"] == expected, category
                if category == "all":
                    assert [item["row_number"] for item in page["items"]] == [2, 3, 4, 5]
                elif category == "warning":
                    assert [item["row_number"] for item in page["items"]] == [3, 4]
                elif category == "attention":
                    assert [item["row_number"] for item in page["items"]] == [5, 3, 4]

            operator_rows = await harness.client.get(
                f"/api/v1/import-jobs/{import_job_id}/rows?category=warning"
            )
            assert operator_rows.status_code == 200, operator_rows.text
            assert "nested-secret@example.com" in operator_rows.text
            assert "13800138000" in operator_rows.text

            set_context(harness, "manager")
            manager_rows = await harness.client.get(
                f"/api/v1/import-jobs/{import_job_id}/rows?category=warning"
            )
            assert manager_rows.status_code == 200, manager_rows.text
            assert "nested-secret@example.com" in manager_rows.text
            assert "13800138000" in manager_rows.text
            set_context(harness, "operator")

            conflicting = await harness.client.get(
                f"/api/v1/import-jobs/{import_job_id}/rows?action=create&category=new"
            )
            assert_error_envelope(conflicting, status_code=422, code="VALIDATION_ERROR")
            for query in (
                "category=new&category=warning",
                "action=create&action=update",
                "offset=0&offset=1",
                "limit=50&limit=100",
            ):
                duplicate = await harness.client.get(
                    f"/api/v1/import-jobs/{import_job_id}/rows?{query}"
                )
                assert_error_envelope(duplicate, status_code=422, code="VALIDATION_ERROR")

            set_context(harness, "viewer")
            viewer_rows = await harness.client.get(
                f"/api/v1/import-jobs/{import_job_id}/rows?category=warning"
            )
            assert viewer_rows.status_code == 200, viewer_rows.text
            viewer_page = assert_success_envelope(viewer_rows)["data"]
            viewer_warning = next(item for item in viewer_page["items"] if item["row_number"] == 3)
            viewer_profile = viewer_warning["normalized_data"]["public_profile"]
            assert viewer_warning["raw_data"] == {"redacted": True}
            assert viewer_warning["normalized_data"]["contacts"] == []
            assert viewer_profile["bio"] == "***"
            assert viewer_profile["details"] == [
                {"phone_note": "***"},
                {"wechat_note": "***"},
                {"observed_at": "2026-08-10T10:00:00+08:00"},
                {"score": "98.123456789"},
            ]
            assert "viewer-secret@example.com" not in viewer_rows.text
            assert "nested-secret@example.com" not in viewer_rows.text
            assert "13800138000" not in viewer_rows.text
            assert "2026-08-10T10:00:00+08:00" in viewer_rows.text
            assert "98.123456789" in viewer_rows.text

            for mutation_path in ("preview", "retry", "cancel"):
                viewer_mutation = await harness.client.post(
                    f"/api/v1/import-jobs/{import_job_id}/{mutation_path}"
                )
                assert_error_envelope(
                    viewer_mutation,
                    status_code=403,
                    code="PERMISSION_DENIED",
                )

            set_context(harness, "cross_department")
            hidden_rows = await harness.client.get(f"/api/v1/import-jobs/{import_job_id}/rows")
            assert_error_envelope(
                hidden_rows,
                status_code=403,
                code="PERMISSION_DENIED",
            )
            set_context(harness, "operator")

            audits_before_reads = int(
                await harness.session.scalar(select(func.count()).select_from(AuditLog)) or 0
            )
            summary_read = await harness.client.get(f"/api/v1/import-jobs/{import_job_id}")
            assert summary_read.status_code == 200, summary_read.text
            assert assert_success_envelope(summary_read)["data"]["preview_summary"] == {
                "raw_rows": 4
            }
            rows_read = await harness.client.get(f"/api/v1/import-jobs/{import_job_id}/rows")
            assert rows_read.status_code == 200, rows_read.text
            audits_after_reads = int(
                await harness.session.scalar(select(func.count()).select_from(AuditLog)) or 0
            )
            assert audits_after_reads == audits_before_reads

            bulk_confirm = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/confirm",
                json={"preview_revision": 1},
            )
            assert_error_envelope(
                bulk_confirm,
                status_code=409,
                code="BULK_CONFIRM_NOT_AVAILABLE",
            )

            delayed_retry = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/preview"
            )
            assert delayed_retry.status_code == 200, delayed_retry.text
            delayed_data = assert_success_envelope(delayed_retry)["data"]
            assert delayed_data["preview_revision"] == 1
            assert delayed_data["task_id"] == first_data["task_id"]
            assert delayed_data["idempotent"] is True
            assert harness.dispatcher.preview_calls == [
                (import_job_id, first_data["task_id"]),
            ]

            explicit_rebuild = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/preview",
                json={"rebuild": True},
            )
            assert explicit_rebuild.status_code == 202, explicit_rebuild.text
            rebuild_data = assert_success_envelope(explicit_rebuild)["data"]
            assert rebuild_data["preview_revision"] == 1
            assert rebuild_data["task_id"] != first_data["task_id"]
            assert rebuild_data["idempotent"] is False
            assert harness.dispatcher.preview_calls[-1] == (
                import_job_id,
                rebuild_data["task_id"],
            )

    asyncio.run(scenario())


def test_collection_job_screening_rules_are_versioned_validated_and_role_guarded() -> None:
    async def scenario() -> None:
        async with bulk_http_harness() as harness:
            collection_id = await create_collection(
                harness.client,
                name="Screening Rules HTTP",
            )
            fetched = await harness.client.get(f"/api/v1/collection-jobs/{collection_id}")
            fetched_data = assert_success_envelope(fetched)["data"]
            assert fetched_data["screening_rules"] == {
                "schema_version": 1,
                "platforms": [],
                "source_tags_exact_any": [],
            }
            assert fetched_data["screening_rules_revision"] == 1

            bulk = await create_bulk(harness.client, collection_id)
            bulk_job_id = UUID(assert_success_envelope(bulk)["data"]["id"])
            bulk_job = await harness.session.get(ImportJob, bulk_job_id)
            assert bulk_job is not None
            frozen_summary = {"raw_rows": 17, "rule_hash": "frozen-before-update"}
            bulk_job.status = ImportJobStatus.PREVIEW_READY
            bulk_job.preview_revision = 3
            bulk_job.preview_summary = frozen_summary
            await harness.session.commit()

            payload = {
                "screening_rules": {
                    "schema_version": 1,
                    "platforms": ["xiaohongshu"],
                    "source_tags_exact_any": ["  Beauty美妆  "],
                },
                "follower_min": 0,
                "follower_max": 200_000,
                "expected_revision": 1,
            }
            updated = await harness.client.put(
                f"/api/v1/collection-jobs/{collection_id}/screening-rules",
                json=payload,
            )
            updated_data = assert_success_envelope(updated)["data"]
            assert updated_data["screening_rules"] == {
                "schema_version": 1,
                "platforms": ["xiaohongshu"],
                "source_tags_exact_any": ["Beauty美妆"],
            }
            assert updated_data["follower_min"] == 0
            assert updated_data["follower_max"] == 200_000
            assert updated_data["screening_rules_revision"] == 2

            await harness.session.refresh(bulk_job)
            assert bulk_job.status is ImportJobStatus.PREVIEW_STALE
            assert bulk_job.preview_revision == 3
            assert bulk_job.preview_summary == frozen_summary

            stale = await harness.client.put(
                f"/api/v1/collection-jobs/{collection_id}/screening-rules",
                json=payload,
            )
            assert_error_envelope(
                stale,
                status_code=409,
                code="SCREENING_RULES_REVISION_CONFLICT",
            )

            invalid = await harness.client.put(
                f"/api/v1/collection-jobs/{collection_id}/screening-rules",
                json={
                    "screening_rules": {
                        "schema_version": 1,
                        "source_tags_exact_any": ["Beauty美妆", " Beauty美妆 "],
                    },
                    "expected_revision": 2,
                },
            )
            assert_error_envelope(invalid, status_code=422, code="VALIDATION_ERROR")

            set_context(harness, "viewer")
            viewer = await harness.client.put(
                f"/api/v1/collection-jobs/{collection_id}/screening-rules",
                json={
                    "screening_rules": {"schema_version": 1},
                    "expected_revision": 2,
                },
            )
            assert_error_envelope(viewer, status_code=403, code="PERMISSION_DENIED")

    asyncio.run(scenario())


def test_bulk_preview_freeze_gates_report_exact_blocking_files() -> None:
    async def scenario() -> None:
        async with bulk_http_harness() as harness:
            collection_id = await create_collection(harness.client, name="Preview Freeze Gates")
            created = await create_bulk(harness.client, collection_id)
            import_job_id = UUID(assert_success_envelope(created)["data"]["id"])

            empty = await harness.client.post(f"/api/v1/import-jobs/{import_job_id}/preview")
            assert_error_envelope(empty, status_code=409, code="IMPORT_PREVIEW_EMPTY")

            uploaded = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="freeze-file",
                filename="freeze.csv",
                content=valid_csv("freeze"),
                content_type="text/csv",
            )
            occurrence_id = UUID(upload_result(uploaded)[0]["id"])
            occurrence = await harness.session.get(ImportJobFile, occurrence_id)
            assert occurrence is not None

            for blocking_status in (
                ImportJobFileStatus.PARSING,
                ImportJobFileStatus.MAPPING_REQUIRED,
                ImportJobFileStatus.FAILED,
            ):
                occurrence.status = blocking_status
                await harness.session.commit()
                blocked = await harness.client.post(f"/api/v1/import-jobs/{import_job_id}/preview")
                body = assert_error_envelope(
                    blocked,
                    status_code=409,
                    code="IMPORT_PREVIEW_BLOCKED",
                )
                assert body["error"]["details"] == {
                    "files": [{"id": str(occurrence_id), "status": blocking_status.value}]
                }

            occurrence.status = ImportJobFileStatus.READY
            occurrence.source_acquired_at_confirmation_required = True
            await harness.session.commit()
            confirmation = await harness.client.post(f"/api/v1/import-jobs/{import_job_id}/preview")
            body = assert_error_envelope(
                confirmation,
                status_code=409,
                code="SOURCE_ACQUIRED_AT_CONFIRMATION_REQUIRED",
            )
            assert body["error"]["details"] == {"file_ids": [str(occurrence_id)]}

            occurrence.source_acquired_at_confirmation_required = False
            occurrence.status = ImportJobFileStatus.EXCLUDED
            await harness.session.commit()
            excluded_only = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/preview"
            )
            assert_error_envelope(
                excluded_only,
                status_code=409,
                code="IMPORT_PREVIEW_EMPTY",
            )

    asyncio.run(scenario())


def test_bulk_preview_retry_is_idempotent_and_rejects_confirm_stage() -> None:
    async def scenario() -> None:
        async with bulk_http_harness() as harness:
            collection_id = await create_collection(harness.client, name="Bulk Retry HTTP")
            created = await create_bulk(harness.client, collection_id)
            import_job_id = UUID(created.json()["data"]["id"])
            uploaded = await upload_file(
                harness.client,
                import_job_id,
                client_file_id="retry-file",
                filename="retry.csv",
                content=valid_csv("retry"),
                content_type="text/csv",
                source_acquired_at="2026-08-10T10:00:00+08:00",
            )
            occurrence = await harness.session.get(
                ImportJobFile,
                UUID(upload_result(uploaded)[0]["id"]),
            )
            job = await harness.session.get(ImportJob, import_job_id)
            assert occurrence is not None and job is not None
            occurrence.status = ImportJobFileStatus.READY
            await harness.session.commit()

            harness.dispatcher.fail_preview = True
            dispatch_failed = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/preview"
            )
            assert_error_envelope(
                dispatch_failed,
                status_code=503,
                code="TASK_DISPATCH_FAILED",
            )
            await harness.session.refresh(job)
            await harness.session.refresh(occurrence)
            assert job.status is ImportJobStatus.FAILED
            assert job.failed_stage is ImportJobFailedStage.PREVIEW
            assert occurrence.status is ImportJobFileStatus.READY
            assert len(harness.dispatcher.preview_calls) == 1
            harness.dispatcher.fail_preview = False

            wrong_endpoint = await harness.client.post(
                f"/api/v1/import-jobs/{import_job_id}/preview"
            )
            assert_error_envelope(
                wrong_endpoint,
                status_code=409,
                code="INVALID_STATE_TRANSITION",
            )
            assert len(harness.dispatcher.preview_calls) == 1

            first = await harness.client.post(f"/api/v1/import-jobs/{import_job_id}/retry")
            assert first.status_code == 202, first.text
            first_data = assert_success_envelope(first)["data"]
            assert first_data["status"] == "previewing"
            assert first_data["idempotent"] is False
            assert harness.dispatcher.preview_calls[-1] == (
                import_job_id,
                first_data["task_id"],
            )

            replay = await harness.client.post(f"/api/v1/import-jobs/{import_job_id}/retry")
            assert replay.status_code == 200, replay.text
            replay_data = assert_success_envelope(replay)["data"]
            assert replay_data["task_id"] == first_data["task_id"]
            assert replay_data["idempotent"] is True
            assert len(harness.dispatcher.preview_calls) == 2

            job.status = ImportJobStatus.FAILED
            job.failed_stage = ImportJobFailedStage.CONFIRM
            await harness.session.commit()
            confirm_stage = await harness.client.post(f"/api/v1/import-jobs/{import_job_id}/retry")
            assert_error_envelope(
                confirm_stage,
                status_code=409,
                code="INVALID_STATE_TRANSITION",
            )
            assert len(harness.dispatcher.preview_calls) == 2

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
                                await client.put(
                                    f"/api/v1/collection-jobs/{collection_id}/screening-rules",
                                    json={
                                        "screening_rules": {"schema_version": 1},
                                        "expected_revision": 1,
                                    },
                                ),
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
                                await client.post(f"/api/v1/import-jobs/{import_job_id}/preview"),
                                await client.post(f"/api/v1/import-jobs/{import_job_id}/retry"),
                                await client.post(
                                    f"/api/v1/import-jobs/{import_job_id}/confirm",
                                    json={"preview_revision": 1},
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
    job_detail = paths["/api/v1/import-jobs/{import_job_id}"]["get"]
    preview = paths["/api/v1/import-jobs/{import_job_id}/preview"]["post"]
    retry_preview = paths["/api/v1/import-jobs/{import_job_id}/retry"]["post"]
    rows = paths["/api/v1/import-jobs/{import_job_id}/rows"]["get"]

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

    detail_envelope = _response_schema(document, job_detail, 200)
    detail_data = _resolve_openapi_schema(document, detail_envelope["properties"]["data"])
    assert {"preview_revision", "preview_summary"} <= set(detail_data["properties"])

    for status_code in (200, 202):
        preview_envelope = _response_schema(document, preview, status_code)
        preview_data = _resolve_openapi_schema(document, preview_envelope["properties"]["data"])
        assert {"import_job_id", "status", "preview_revision", "task_id", "idempotent"} <= set(
            preview_data["properties"]
        )
    preview_request = preview["requestBody"]["content"]["application/json"]["schema"]
    if "anyOf" in preview_request:
        preview_request = next(item for item in preview_request["anyOf"] if "$ref" in item)
    preview_request = _resolve_openapi_schema(document, preview_request)
    assert preview_request["properties"]["rebuild"] == {
        "type": "boolean",
        "title": "Rebuild",
        "default": False,
    }
    assert preview_request["additionalProperties"] is False
    assert {"200", "202", "401", "403", "404", "409", "422", "503"} <= set(preview["responses"])
    for status_code in (200, 202):
        retry_envelope = _response_schema(document, retry_preview, status_code)
        retry_data = _resolve_openapi_schema(document, retry_envelope["properties"]["data"])
        assert {"import_job_id", "status", "preview_revision", "task_id", "idempotent"} <= set(
            retry_data["properties"]
        )
    assert {"200", "202", "401", "403", "404", "409", "422", "503"} <= set(
        retry_preview["responses"]
    )

    rows_envelope = _response_schema(document, rows, 200)
    rows_data = _resolve_openapi_schema(document, rows_envelope["properties"]["data"])
    assert {"items", "total", "offset", "limit"} <= set(rows_data["properties"])
    category_parameter = next(
        parameter for parameter in rows["parameters"] if parameter["name"] == "category"
    )
    category_schema = category_parameter["schema"]
    if "anyOf" in category_schema:
        category_schema = next(item for item in category_schema["anyOf"] if "$ref" in item)
    category_schema = _resolve_openapi_schema(document, category_schema)
    assert set(category_schema["enum"]) == {
        "attention",
        "error",
        "manual_review",
        "warning",
        "changed",
        "new",
        "no_change",
        "duplicate",
        "all",
    }

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


def test_collection_screening_openapi_is_strict_typed_and_versioned() -> None:
    document = app.openapi()
    operation = document["paths"]["/api/v1/collection-jobs/{collection_job_id}/screening-rules"][
        "put"
    ]
    request_schema = _resolve_openapi_schema(
        document,
        operation["requestBody"]["content"]["application/json"]["schema"],
    )
    assert set(request_schema["required"]) == {"screening_rules", "expected_revision"}
    assert {
        "screening_rules",
        "follower_min",
        "follower_max",
        "expected_revision",
    } == set(request_schema["properties"])

    rules_schema = _resolve_openapi_schema(
        document,
        request_schema["properties"]["screening_rules"],
    )
    assert {"schema_version", "platforms", "source_tags_exact_any"} == set(
        rules_schema["properties"]
    )
    platform_schema = _resolve_openapi_schema(
        document,
        rules_schema["properties"]["platforms"]["items"],
    )
    assert set(platform_schema["enum"]) == {"xiaohongshu"}
    tag_schema = _resolve_openapi_schema(
        document,
        rules_schema["properties"]["source_tags_exact_any"]["items"],
    )
    assert tag_schema["minLength"] == 1
    assert tag_schema["maxLength"] == 160

    response_envelope = _response_schema(document, operation, 200)
    response_data = _resolve_openapi_schema(
        document,
        response_envelope["properties"]["data"],
    )
    assert {"screening_rules", "screening_rules_revision"} <= set(response_data["properties"])
    assert {"200", "401", "403", "404", "409", "422"} <= set(operation["responses"])


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
