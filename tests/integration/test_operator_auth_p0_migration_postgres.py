"""PostgreSQL 16 migration gate for the Operator Auth P0 hotfix.

The module runs only against an explicitly disposable ``phase1b_test``
database.  Every test uses a random schema and upgrades specifically from the
Permissions V1 0010 baseline.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from backend_core.auth.enums import OperatorStatus, Role
from backend_core.auth.errors import AuthError
from backend_core.auth.models import Department
from backend_core.auth.operator_admin_service import OperatorAdminService
from backend_core.auth.repository import AuthRepository
from backend_core.auth.schemas import OperatorAdminCreateInput, OperatorAdminUpdateInput
from backend_core.auth.security import hash_password, hash_token, verify_password
from backend_core.auth.service import AuthContext, AuthService, OperatorCredentialSetupService
from backend_core.auth.throttle import RedisLoginThrottle
from backend_core.config import get_settings
from fakeredis.aioredis import FakeRedis
from sqlalchemy import Connection, Engine, create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "infrastructure" / "migrations" / "alembic.ini"
MIGRATIONS = PROJECT_ROOT / "infrastructure" / "migrations"
PERMISSIONS_REVISION = "0010_permissions_v1_persistence"
OPERATOR_AUTH_REVISION = "0011_operator_auth_p0"
CURRENT_HEAD_REVISION = "0013_market_prospect_rules_v1_provisional"
OPERATOR_AUTH_AUDIT_ACTIONS = (
    "OPERATOR_AUTHENTICATED",
    "OPERATOR_AUTH_FAILED",
    "OPERATOR_CREDENTIAL_SET",
    "OPERATOR_PASSWORD_RESET",
)
PG_CONCURRENCY_TIMEOUT_SECONDS = 15.0
DOWNGRADE_LOCK_OBSERVATION_TIMEOUT_SECONDS = 5.0
DOWNGRADE_LOCK_TIMEOUT_MS = 8_000
DOWNGRADE_STATEMENT_TIMEOUT_MS = 12_000
DOWNGRADE_FUTURE_TIMEOUT_SECONDS = 15.0


def _gated_test_database_url() -> URL:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("TEST_DATABASE_URL is not set", allow_module_level=True)
    try:
        url = make_url(raw_url)
    except ArgumentError as error:
        pytest.fail(f"TEST_DATABASE_URL is invalid: {error}", pytrace=False)
    database_name = (url.database or "").lower()
    if url.get_backend_name() != "postgresql" or "phase1b_test" not in database_name:
        pytest.fail(
            "TEST_DATABASE_URL must be PostgreSQL and its database name must contain "
            "'phase1b_test'",
            pytrace=False,
        )
    return url.set(drivername="postgresql+psycopg")


TEST_DATABASE_URL = _gated_test_database_url()


class MigrationDatabase:
    def __init__(self, engine: Engine, config: Config) -> None:
        self.engine = engine
        self.config = config

    def upgrade(self, revision: str) -> None:
        get_settings.cache_clear()
        command.upgrade(self.config, revision)

    def downgrade(self, revision: str) -> None:
        get_settings.cache_clear()
        command.downgrade(self.config, revision)


@dataclass(frozen=True, slots=True)
class RuntimePopulation:
    department_id: UUID
    actor_id: UUID
    peer_admin_id: UUID
    reset_target_id: UUID
    legacy_target_id: UUID
    actor_session_token: str
    peer_session_token: str
    unbound_session_token: str
    actor_password: str
    peer_password: str
    reset_target_password: str
    department_password: str


@pytest.fixture
def migration_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[MigrationDatabase]:
    schema_name = f"operator_auth_p0_migration_{uuid4().hex}"
    admin_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(CreateSchema(schema_name))

    scoped_url = TEST_DATABASE_URL.update_query_dict({"options": f"-csearch_path={schema_name}"})
    database_url = scoped_url.render_as_string(hide_password=False).replace("%", "%%")
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS))
    engine = create_engine(scoped_url, pool_pre_ping=True)
    try:
        yield MigrationDatabase(engine, config)
    finally:
        engine.dispose()
        get_settings.cache_clear()
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema_name, cascade=True, if_exists=True))
        admin_engine.dispose()


def _assert_postgresql_16(connection: Connection) -> None:
    assert int(connection.scalar(text("SHOW server_version_num"))) // 10_000 == 16


def _revision(connection: Connection) -> str:
    return str(connection.scalar(text("SELECT version_num FROM alembic_version")))


def _enum_values(connection: Connection, enum_name: str) -> list[str]:
    return list(
        connection.scalars(
            text(
                """
                SELECT enum.enumlabel
                FROM pg_enum AS enum
                JOIN pg_type AS type ON type.oid = enum.enumtypid
                JOIN pg_namespace AS namespace ON namespace.oid = type.typnamespace
                WHERE namespace.nspname = current_schema()
                  AND type.typname = :enum_name
                ORDER BY enum.enumsortorder
                """
            ),
            {"enum_name": enum_name},
        )
    )


def _seed_legacy_bound_operator(connection: Connection) -> dict[str, UUID]:
    department_id = uuid4()
    operator_id = uuid4()
    session_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO departments (id, name, password_hash, status, session_days)
            VALUES (:id, :name, 'legacy-department-password-hash',
                    'active'::department_status, 30)
            """
        ),
        {"id": department_id, "name": f"Operator Auth migration {department_id}"},
    )
    connection.execute(
        text(
            """
            INSERT INTO department_permissions (id, department_id, role)
            VALUES (:id, :department_id, 'operator'::role_enum)
            """
        ),
        {"id": uuid4(), "department_id": department_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO operators (id, department_id, name, role, status)
            VALUES (:id, :department_id, :name, 'operator'::role_enum,
                    'active'::operator_status)
            """
        ),
        {
            "id": operator_id,
            "department_id": department_id,
            "name": f"Legacy Operator {operator_id}",
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO operator_module_permissions (operator_id, department_id, module_key)
            VALUES (:operator_id, :department_id, 'campaigns')
            """
        ),
        {"operator_id": operator_id, "department_id": department_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO sessions (
                id, department_id, operator_id, token_hash, csrf_token_hash,
                ip, user_agent, expires_at
            ) VALUES (
                :id, :department_id, :operator_id, :token_hash, :csrf_token_hash,
                '127.0.0.1', 'operator-auth-migration-test', :expires_at
            )
            """
        ),
        {
            "id": session_id,
            "department_id": department_id,
            "operator_id": operator_id,
            "token_hash": "a" * 64,
            "csrf_token_hash": "b" * 64,
            "expires_at": datetime.now(UTC) + timedelta(days=30),
        },
    )
    return {
        "department_id": department_id,
        "operator_id": operator_id,
        "session_id": session_id,
    }


def _seed_legacy_super_admin_population(
    connection: Connection,
    *,
    department_password_hash: str,
    legacy_session_token_hash: str,
) -> dict[str, UUID]:
    department_id = uuid4()
    credential_ready_operator_id = uuid4()
    credentialless_operator_id = uuid4()
    legacy_session_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO departments (id, name, password_hash, status, session_days)
            VALUES (:id, :name, :password_hash, 'active'::department_status, 30)
            """
        ),
        {
            "id": department_id,
            "name": f"Operator Auth Super Admin migration {department_id}",
            "password_hash": department_password_hash,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO department_permissions (id, department_id, role)
            VALUES (:id, :department_id, 'super_admin'::role_enum)
            """
        ),
        {"id": uuid4(), "department_id": department_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO operators (id, department_id, name, role, status)
            VALUES
                (:credential_ready_id, :department_id, 'Legacy Credential-Ready SA',
                 'super_admin'::role_enum, 'active'::operator_status),
                (:credentialless_id, :department_id, 'Legacy Credentialless SA',
                 'super_admin'::role_enum, 'active'::operator_status)
            """
        ),
        {
            "credential_ready_id": credential_ready_operator_id,
            "credentialless_id": credentialless_operator_id,
            "department_id": department_id,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO sessions (
                id, department_id, operator_id, token_hash, csrf_token_hash,
                ip, user_agent, expires_at
            ) VALUES (
                :id, :department_id, :operator_id, :token_hash, :csrf_token_hash,
                '127.0.0.1', 'legacy-super-admin-session', :expires_at
            )
            """
        ),
        {
            "id": legacy_session_id,
            "department_id": department_id,
            "operator_id": credential_ready_operator_id,
            "token_hash": legacy_session_token_hash,
            "csrf_token_hash": hash_token("legacy-super-admin-csrf"),
            "expires_at": datetime.now(UTC) + timedelta(days=30),
        },
    )
    return {
        "department_id": department_id,
        "credential_ready_operator_id": credential_ready_operator_id,
        "credentialless_operator_id": credentialless_operator_id,
        "legacy_session_id": legacy_session_id,
    }


def _seed_runtime_population(connection: Connection) -> RuntimePopulation:
    department_id = uuid4()
    actor_id = uuid4()
    peer_admin_id = uuid4()
    reset_target_id = uuid4()
    legacy_target_id = uuid4()
    actor_session_id = uuid4()
    peer_session_id = uuid4()
    unbound_session_id = uuid4()
    department_password = "postgres-runtime-department-password"
    actor_password = "postgres-runtime-actor-password"
    peer_password = "postgres-runtime-peer-password"
    reset_target_password = "postgres-runtime-reset-target-password"
    actor_session_token = f"postgres-runtime-actor-session-{uuid4()}"
    peer_session_token = f"postgres-runtime-peer-session-{uuid4()}"
    unbound_session_token = f"postgres-runtime-unbound-session-{uuid4()}"
    expires_at = datetime.now(UTC) + timedelta(days=1)

    connection.execute(
        text(
            """
            INSERT INTO departments (id, name, password_hash, status, session_days)
            VALUES (:id, :name, :password_hash, 'active'::department_status, 30)
            """
        ),
        {
            "id": department_id,
            "name": f"Operator Auth Runtime {department_id}",
            "password_hash": hash_password(department_password),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO department_permissions (id, department_id, role)
            VALUES (:id, :department_id, 'super_admin'::role_enum)
            """
        ),
        {"id": uuid4(), "department_id": department_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO operators (
                id, department_id, name, role, status, password_hash, credential_version
            ) VALUES
                (:actor_id, :department_id, 'Runtime Actor', 'super_admin'::role_enum,
                 'active'::operator_status, :actor_hash, 1),
                (:peer_id, :department_id, 'Runtime Peer Admin', 'super_admin'::role_enum,
                 'active'::operator_status, :peer_hash, 1),
                (:reset_target_id, :department_id, 'Runtime Reset Target',
                 'operator'::role_enum, 'active'::operator_status, :reset_target_hash, 1),
                (:legacy_target_id, :department_id, 'Runtime Legacy Target',
                 'operator'::role_enum, 'active'::operator_status, NULL, 0)
            """
        ),
        {
            "actor_id": actor_id,
            "peer_id": peer_admin_id,
            "reset_target_id": reset_target_id,
            "legacy_target_id": legacy_target_id,
            "department_id": department_id,
            "actor_hash": hash_password(actor_password),
            "peer_hash": hash_password(peer_password),
            "reset_target_hash": hash_password(reset_target_password),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO sessions (
                id, department_id, operator_id, operator_credential_version,
                token_hash, csrf_token_hash, ip, user_agent, expires_at
            ) VALUES
                (:actor_session_id, :department_id, :actor_id, 1,
                 :actor_token_hash, :actor_csrf_hash,
                 '192.0.2.201', 'runtime-actor-provenance', :expires_at),
                (:peer_session_id, :department_id, :peer_id, 1,
                 :peer_token_hash, :peer_csrf_hash,
                 '192.0.2.202', 'runtime-peer-provenance', :expires_at),
                (:unbound_session_id, :department_id, NULL, NULL,
                 :unbound_token_hash, :unbound_csrf_hash,
                 '192.0.2.203', 'runtime-unbound-provenance', :expires_at)
            """
        ),
        {
            "actor_session_id": actor_session_id,
            "peer_session_id": peer_session_id,
            "unbound_session_id": unbound_session_id,
            "department_id": department_id,
            "actor_id": actor_id,
            "peer_id": peer_admin_id,
            "actor_token_hash": hash_token(actor_session_token),
            "actor_csrf_hash": hash_token("runtime-actor-csrf"),
            "peer_token_hash": hash_token(peer_session_token),
            "peer_csrf_hash": hash_token("runtime-peer-csrf"),
            "unbound_token_hash": hash_token(unbound_session_token),
            "unbound_csrf_hash": hash_token("runtime-unbound-csrf"),
            "expires_at": expires_at,
        },
    )
    return RuntimePopulation(
        department_id=department_id,
        actor_id=actor_id,
        peer_admin_id=peer_admin_id,
        reset_target_id=reset_target_id,
        legacy_target_id=legacy_target_id,
        actor_session_token=actor_session_token,
        peer_session_token=peer_session_token,
        unbound_session_token=unbound_session_token,
        actor_password=actor_password,
        peer_password=peer_password,
        reset_target_password=reset_target_password,
        department_password=department_password,
    )


def _prepare_runtime_population(migration_database: MigrationDatabase) -> RuntimePopulation:
    migration_database.upgrade(PERMISSIONS_REVISION)
    migration_database.upgrade(OPERATOR_AUTH_REVISION)
    with migration_database.engine.begin() as connection:
        _assert_postgresql_16(connection)
        return _seed_runtime_population(connection)


async def _wait_for_event(event: asyncio.Event, *, label: str) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout=PG_CONCURRENCY_TIMEOUT_SECONDS)
    except TimeoutError:
        pytest.fail(f"Timed out waiting for {label}", pytrace=False)


async def _wait_for_task[TaskResultT](
    task: asyncio.Task[TaskResultT],
    *,
    label: str,
) -> TaskResultT:
    try:
        return await asyncio.wait_for(task, timeout=PG_CONCURRENCY_TIMEOUT_SECONDS)
    except TimeoutError:
        pytest.fail(f"Timed out waiting for {label}", pytrace=False)


def _pause_after_department_lock(
    repository: AuthRepository,
    acquired: asyncio.Event,
    release: asyncio.Event,
) -> None:
    original = repository.lock_departments_for_update

    async def paused(department_ids: Iterable[UUID]) -> dict[UUID, Department]:
        locked = await original(department_ids)
        acquired.set()
        await _wait_for_event(release, label="test release after Department lock")
        return locked

    repository.lock_departments_for_update = paused  # type: ignore[method-assign]


def _signal_department_lock_attempt(
    repository: AuthRepository,
    attempted: asyncio.Event,
) -> None:
    original = repository.lock_departments_for_update

    async def signaled(department_ids: Iterable[UUID]) -> dict[UUID, Department]:
        attempted.set()
        return await original(department_ids)

    repository.lock_departments_for_update = signaled  # type: ignore[method-assign]


async def _authenticate_runtime(
    session: AsyncSession,
    redis: FakeRedis,
    token: str,
) -> tuple[AuthService, AuthContext]:
    service = AuthService(
        session,
        RedisLoginThrottle(redis, max_failures=5, lock_seconds=300),
    )
    return service, await service.authenticate(token)


def _column_names(connection: Connection, table_name: str) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns(table_name)}


def test_upgrade_preserves_legacy_rows_and_marks_credentials_uninitialized(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade(PERMISSIONS_REVISION)
    with migration_database.engine.begin() as connection:
        seeded = _seed_legacy_bound_operator(connection)

    migration_database.upgrade(OPERATOR_AUTH_REVISION)
    migration_database.upgrade("head")
    with migration_database.engine.connect() as connection:
        _assert_postgresql_16(connection)
        assert _revision(connection) == CURRENT_HEAD_REVISION
        assert ScriptDirectory.from_config(migration_database.config).get_heads() == [
            CURRENT_HEAD_REVISION
        ]
        operator_columns = {
            column["name"]: column for column in inspect(connection).get_columns("operators")
        }
        session_columns = {
            column["name"]: column for column in inspect(connection).get_columns("sessions")
        }
        assert operator_columns["password_hash"]["nullable"] is True
        assert operator_columns["credential_version"]["nullable"] is False
        assert operator_columns["credential_version"]["default"] == "0"
        assert session_columns["operator_credential_version"]["nullable"] is True
        operator_row = connection.execute(
            text(
                """
                SELECT department_id, name, role::text, status::text,
                       password_hash, credential_version
                FROM operators
                WHERE id = :operator_id
                """
            ),
            {"operator_id": seeded["operator_id"]},
        ).one()
        assert operator_row.department_id == seeded["department_id"]
        assert operator_row.name == f"Legacy Operator {seeded['operator_id']}"
        assert operator_row.role == "operator"
        assert operator_row.status == "active"
        assert operator_row.password_hash is None
        assert operator_row.credential_version == 0
        assert (
            connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM operator_module_permissions
                    WHERE operator_id = :operator_id
                      AND department_id = :department_id
                      AND module_key = 'campaigns'
                    """
                ),
                seeded,
            )
            == 1
        )
        session_row = connection.execute(
            text(
                """
                SELECT department_id, operator_id, operator_credential_version,
                       token_hash, revoked_at
                FROM sessions
                WHERE id = :session_id
                """
            ),
            {"session_id": seeded["session_id"]},
        ).one()
        assert session_row.department_id == seeded["department_id"]
        assert session_row.operator_id == seeded["operator_id"]
        assert session_row.operator_credential_version is None
        assert session_row.token_hash == "a" * 64
        assert session_row.revoked_at is None
        assert set(OPERATOR_AUTH_AUDIT_ACTIONS) <= set(_enum_values(connection, "audit_action"))


def test_uninitialized_credentials_allow_safe_downgrade_and_reupgrade(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade(PERMISSIONS_REVISION)
    with migration_database.engine.begin() as connection:
        seeded = _seed_legacy_bound_operator(connection)
    migration_database.upgrade(OPERATOR_AUTH_REVISION)

    migration_database.downgrade(PERMISSIONS_REVISION)
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == PERMISSIONS_REVISION
        assert "password_hash" not in _column_names(connection, "operators")
        assert "credential_version" not in _column_names(connection, "operators")
        assert "operator_credential_version" not in _column_names(connection, "sessions")
        assert (
            connection.scalar(
                text("SELECT count(*) FROM operators WHERE id = :operator_id"),
                {"operator_id": seeded["operator_id"]},
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM operator_module_permissions "
                    "WHERE operator_id = :operator_id AND module_key = 'campaigns'"
                ),
                {"operator_id": seeded["operator_id"]},
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM sessions "
                    "WHERE id = :session_id AND operator_id = :operator_id"
                ),
                {
                    "session_id": seeded["session_id"],
                    "operator_id": seeded["operator_id"],
                },
            )
            == 1
        )
        assert set(OPERATOR_AUTH_AUDIT_ACTIONS) <= set(_enum_values(connection, "audit_action"))

    migration_database.upgrade(OPERATOR_AUTH_REVISION)
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == OPERATOR_AUTH_REVISION
        assert (
            connection.execute(
                text(
                    """
                SELECT password_hash, credential_version
                FROM operators
                WHERE id = :operator_id
                """
                ),
                {"operator_id": seeded["operator_id"]},
            ).one()
            == (None, 0)
        )
        assert (
            connection.scalar(
                text("SELECT operator_credential_version FROM sessions WHERE id = :session_id"),
                {"session_id": seeded["session_id"]},
            )
            is None
        )


def test_downgrade_refuses_to_destroy_initialized_operator_credentials(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade(PERMISSIONS_REVISION)
    with migration_database.engine.begin() as connection:
        seeded = _seed_legacy_bound_operator(connection)
    migration_database.upgrade(OPERATOR_AUTH_REVISION)
    with migration_database.engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE operators
                SET password_hash = :password_hash, credential_version = 1
                WHERE id = :operator_id
                """
            ),
            {
                "operator_id": seeded["operator_id"],
                "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$initialized-test-hash",
            },
        )

    with pytest.raises(RuntimeError, match="initialized Operator credentials exist"):
        migration_database.downgrade(PERMISSIONS_REVISION)

    with migration_database.engine.connect() as connection:
        assert _revision(connection) == OPERATOR_AUTH_REVISION
        assert "password_hash" in _column_names(connection, "operators")
        assert (
            connection.scalar(
                text("SELECT credential_version FROM operators WHERE id = :operator_id"),
                {"operator_id": seeded["operator_id"]},
            )
            == 1
        )


def test_production_shaped_super_admin_bootstrap_auth_rotation_and_last_ready_guard(
    migration_database: MigrationDatabase,
) -> None:
    department_password = "production-shaped-department-password"
    operator_password = "production-shaped-operator-password"
    legacy_session_token = "legacy-passwordless-super-admin-session-token"

    migration_database.upgrade(PERMISSIONS_REVISION)
    with migration_database.engine.begin() as connection:
        seeded = _seed_legacy_super_admin_population(
            connection,
            department_password_hash=hash_password(department_password),
            legacy_session_token_hash=hash_token(legacy_session_token),
        )

    migration_database.upgrade(OPERATOR_AUTH_REVISION)
    with migration_database.engine.connect() as connection:
        _assert_postgresql_16(connection)
        assert _revision(connection) == OPERATOR_AUTH_REVISION
        migrated_operators = connection.execute(
            text(
                """
                SELECT id, password_hash, credential_version
                FROM operators
                WHERE id IN (:credential_ready_operator_id, :credentialless_operator_id)
                ORDER BY id
                """
            ),
            seeded,
        ).all()
        assert len(migrated_operators) == 2
        assert all(row.password_hash is None for row in migrated_operators)
        assert all(row.credential_version == 0 for row in migrated_operators)
        assert (
            connection.scalar(
                text(
                    """
                    SELECT operator_credential_version
                    FROM sessions
                    WHERE id = :legacy_session_id
                    """
                ),
                seeded,
            )
            is None
        )

    async def verify_runtime_flow() -> None:
        engine = create_async_engine(migration_database.engine.url, pool_pre_ping=True)
        redis = FakeRedis(decode_responses=True)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                setup_result = await OperatorCredentialSetupService(session).setup(
                    department_id=seeded["department_id"],
                    operator_id=seeded["credential_ready_operator_id"],
                    password=operator_password,
                    require_super_admin=True,
                )
                assert setup_result.operator.id == seeded["credential_ready_operator_id"]
                assert setup_result.operator.password_hash is not None
                assert setup_result.operator.password_hash.startswith("$argon2id$")
                assert setup_result.operator.password_hash != operator_password
                assert verify_password(setup_result.operator.password_hash, operator_password)
                assert not verify_password(
                    setup_result.operator.password_hash,
                    department_password,
                )
                assert setup_result.operator.credential_version == 1
                assert setup_result.revoked_sessions == 1

                migrated_legacy_session = (
                    await session.execute(
                        text(
                            """
                            SELECT revoked_at, operator_credential_version
                            FROM sessions
                            WHERE id = :legacy_session_id
                            """
                        ),
                        seeded,
                    )
                ).one()
                assert migrated_legacy_session.revoked_at is not None
                assert migrated_legacy_session.operator_credential_version is None

                credentialless_operator = await OperatorCredentialSetupService(
                    session
                ).repository.get_operator_in_department(
                    operator_id=seeded["credentialless_operator_id"],
                    department_id=seeded["department_id"],
                )
                assert credentialless_operator is not None
                assert credentialless_operator.role is Role.SUPER_ADMIN
                assert credentialless_operator.status is OperatorStatus.ACTIVE
                assert credentialless_operator.password_hash is None
                assert credentialless_operator.credential_version == 0

                throttle = RedisLoginThrottle(redis, max_failures=5, lock_seconds=300)
                auth = AuthService(session, throttle)
                department_login = await auth.login(
                    department_id=seeded["department_id"],
                    password=department_password,
                    remember_me=True,
                    ip="192.0.2.111",
                    user_agent="operator-auth-p0-postgres-gate",
                )
                assert department_login.auth_session.operator_id is None
                assert department_login.auth_session.operator_credential_version is None
                department_context = await auth.authenticate(department_login.session_token)
                assert department_context.operator is None
                assert department_context.effective_role is None

                authenticated = await auth.select_operator(
                    department_context,
                    seeded["credential_ready_operator_id"],
                    operator_password,
                    ip="192.0.2.111",
                    user_agent="operator-auth-p0-postgres-gate",
                )
                assert authenticated.session_token != department_login.session_token
                assert authenticated.csrf_token != department_login.csrf_token
                assert authenticated.auth_session.id != department_login.auth_session.id
                assert (
                    authenticated.auth_session.operator_id == seeded["credential_ready_operator_id"]
                )
                assert authenticated.auth_session.operator_credential_version == 1

                await session.refresh(department_login.auth_session)
                assert department_login.auth_session.revoked_at is not None
                for revoked_token in (legacy_session_token, department_login.session_token):
                    with pytest.raises(AuthError) as revoked:
                        await auth.authenticate(revoked_token)
                    assert revoked.value.status_code == 401
                    assert revoked.value.code == "INVALID_SESSION"

                exact_context = await auth.authenticate(authenticated.session_token)
                assert exact_context.department.id == seeded["department_id"]
                assert exact_context.operator is not None
                assert exact_context.operator.id == seeded["credential_ready_operator_id"]
                assert exact_context.operator.role is Role.SUPER_ADMIN
                assert exact_context.department_role_ceiling is Role.SUPER_ADMIN
                assert exact_context.effective_role is Role.SUPER_ADMIN
                assert exact_context.auth_session.id == authenticated.auth_session.id

                admin = OperatorAdminService(session)
                with pytest.raises(AuthError) as disable_last_ready:
                    await admin.update_operator(
                        exact_context,
                        operator_id=seeded["credential_ready_operator_id"],
                        payload=OperatorAdminUpdateInput(
                            expected_updated_at=exact_context.operator.updated_at,
                            status="disabled",
                        ),
                        ip="192.0.2.111",
                        user_agent="operator-auth-p0-postgres-gate",
                    )
                assert disable_last_ready.value.status_code == 409
                assert disable_last_ready.value.code == "LAST_ACTIVE_SUPER_ADMIN"

                exact_context = await auth.authenticate(authenticated.session_token)
                assert exact_context.operator is not None
                with pytest.raises(AuthError) as demote_last_ready:
                    await admin.update_operator(
                        exact_context,
                        operator_id=seeded["credential_ready_operator_id"],
                        payload=OperatorAdminUpdateInput(
                            expected_updated_at=exact_context.operator.updated_at,
                            role="manager",
                        ),
                        ip="192.0.2.111",
                        user_agent="operator-auth-p0-postgres-gate",
                    )
                assert demote_last_ready.value.status_code == 409
                assert demote_last_ready.value.code == "LAST_ACTIVE_SUPER_ADMIN"

                surviving_context = await auth.authenticate(authenticated.session_token)
                assert surviving_context.operator is not None
                assert surviving_context.operator.id == seeded["credential_ready_operator_id"]
                assert surviving_context.operator.role is Role.SUPER_ADMIN
                assert surviving_context.operator.status is OperatorStatus.ACTIVE
                still_credentialless = await auth.repository.get_operator_in_department(
                    operator_id=seeded["credentialless_operator_id"],
                    department_id=seeded["department_id"],
                )
                assert still_credentialless is not None
                assert still_credentialless.role is Role.SUPER_ADMIN
                assert still_credentialless.status is OperatorStatus.ACTIVE
                assert still_credentialless.password_hash is None
                assert still_credentialless.credential_version == 0
        finally:
            await redis.aclose()
            await engine.dispose()

    asyncio.run(verify_runtime_flow())


@pytest.mark.parametrize(
    ("authority_change", "expected_code"),
    (
        pytest.param("demote", "PERMISSION_DENIED", id="demotion"),
        pytest.param("disable", "INVALID_SESSION", id="disablement"),
        pytest.param("reset", "INVALID_SESSION", id="credential-version-reset"),
    ),
)
def test_stale_super_admin_cannot_mutate_after_authority_change(
    migration_database: MigrationDatabase,
    authority_change: str,
    expected_code: str,
) -> None:
    population = _prepare_runtime_population(migration_database)

    async def scenario() -> None:
        engine = create_async_engine(migration_database.engine.url, pool_pre_ping=True)
        redis = FakeRedis(decode_responses=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as stale_session, factory() as changer_session:
                _stale_auth, stale_context = await _authenticate_runtime(
                    stale_session,
                    redis,
                    population.actor_session_token,
                )
                _changer_auth, changer_context = await _authenticate_runtime(
                    changer_session,
                    redis,
                    population.peer_session_token,
                )
                changer = OperatorAdminService(changer_session)
                actor = await changer.repository.get_operator_in_department(
                    operator_id=population.actor_id,
                    department_id=population.department_id,
                )
                assert actor is not None
                if authority_change == "demote":
                    await changer.update_operator(
                        changer_context,
                        operator_id=actor.id,
                        payload=OperatorAdminUpdateInput(
                            expected_updated_at=actor.updated_at,
                            role="manager",
                        ),
                        ip="192.0.2.210",
                        user_agent="postgres-stale-authority-change",
                    )
                elif authority_change == "disable":
                    await changer.update_operator(
                        changer_context,
                        operator_id=actor.id,
                        payload=OperatorAdminUpdateInput(
                            expected_updated_at=actor.updated_at,
                            status="disabled",
                        ),
                        ip="192.0.2.210",
                        user_agent="postgres-stale-authority-change",
                    )
                else:
                    await changer.reset_operator_password(
                        changer_context,
                        operator_id=actor.id,
                        password="postgres-stale-actor-replacement-password",
                        ip="192.0.2.210",
                        user_agent="postgres-stale-authority-change",
                    )

                stale_admin = OperatorAdminService(stale_session)
                forbidden_name = f"Forbidden Stale Admin {authority_change}"
                with pytest.raises(AuthError) as rejected:
                    await stale_admin.create_operator(
                        stale_context,
                        OperatorAdminCreateInput(
                            name=forbidden_name,
                            password="postgres-forbidden-stale-admin-password",
                            role="super_admin",
                            module_grants=[],
                        ),
                        ip="192.0.2.211",
                        user_agent="postgres-stale-authority-mutation",
                    )
                assert rejected.value.code == expected_code
                assert (
                    await stale_session.scalar(
                        text("SELECT count(*) FROM operators WHERE name = :name"),
                        {"name": forbidden_name},
                    )
                    == 0
                )
        finally:
            await redis.aclose()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ("disable", "demote"))
def test_concurrent_last_usable_super_admin_removals_preserve_one_admin(
    migration_database: MigrationDatabase,
    mutation: str,
) -> None:
    population = _prepare_runtime_population(migration_database)

    async def scenario() -> None:
        engine = create_async_engine(migration_database.engine.url, pool_pre_ping=True)
        redis = FakeRedis(decode_responses=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        acquired = asyncio.Event()
        release = asyncio.Event()
        attempted = asyncio.Event()
        try:
            async with factory() as first_session, factory() as second_session:
                _first_auth, first_context = await _authenticate_runtime(
                    first_session,
                    redis,
                    population.actor_session_token,
                )
                _second_auth, second_context = await _authenticate_runtime(
                    second_session,
                    redis,
                    population.peer_session_token,
                )
                assert first_context.operator is not None
                assert second_context.operator is not None
                first_service = OperatorAdminService(first_session)
                second_service = OperatorAdminService(second_session)
                _pause_after_department_lock(first_service.repository, acquired, release)
                _signal_department_lock_attempt(second_service.repository, attempted)

                first_payload = (
                    OperatorAdminUpdateInput(
                        expected_updated_at=first_context.operator.updated_at,
                        status="disabled",
                    )
                    if mutation == "disable"
                    else OperatorAdminUpdateInput(
                        expected_updated_at=first_context.operator.updated_at,
                        role="manager",
                    )
                )
                second_payload = (
                    OperatorAdminUpdateInput(
                        expected_updated_at=second_context.operator.updated_at,
                        status="disabled",
                    )
                    if mutation == "disable"
                    else OperatorAdminUpdateInput(
                        expected_updated_at=second_context.operator.updated_at,
                        role="manager",
                    )
                )

                first_task = asyncio.create_task(
                    first_service.update_operator(
                        first_context,
                        operator_id=population.actor_id,
                        payload=first_payload,
                        ip="192.0.2.220",
                        user_agent=f"postgres-first-self-{mutation}",
                    )
                )
                await _wait_for_event(acquired, label=f"first {mutation} Department lock")
                second_task = asyncio.create_task(
                    second_service.update_operator(
                        second_context,
                        operator_id=population.peer_admin_id,
                        payload=second_payload,
                        ip="192.0.2.221",
                        user_agent=f"postgres-second-self-{mutation}",
                    )
                )
                await _wait_for_event(attempted, label=f"second {mutation} lock attempt")
                assert not second_task.done()
                release.set()

                first_result = await _wait_for_task(
                    first_task,
                    label=f"first {mutation} completion",
                )
                if mutation == "disable":
                    assert first_result.status is OperatorStatus.DISABLED
                else:
                    assert first_result.role is Role.MANAGER
                with pytest.raises(AuthError) as rejected:
                    await _wait_for_task(
                        second_task,
                        label=f"second {mutation} fail-closed completion",
                    )
                assert rejected.value.code == "LAST_ACTIVE_SUPER_ADMIN"

                async with factory() as verify_session:
                    usable_admin_ids = list(
                        await verify_session.scalars(
                            text(
                                """
                                SELECT id
                                FROM operators
                                WHERE department_id = :department_id
                                  AND status = 'active'::operator_status
                                  AND role = 'super_admin'::role_enum
                                  AND password_hash IS NOT NULL
                                  AND credential_version >= 1
                                ORDER BY id
                                """
                            ),
                            {"department_id": population.department_id},
                        )
                    )
                    assert usable_admin_ids == [population.peer_admin_id]
        finally:
            release.set()
            await redis.aclose()
            await engine.dispose()

    asyncio.run(scenario())


def test_department_reset_serializes_against_legacy_operator_setup(
    migration_database: MigrationDatabase,
) -> None:
    population = _prepare_runtime_population(migration_database)

    async def scenario() -> None:
        engine = create_async_engine(migration_database.engine.url, pool_pre_ping=True)
        redis = FakeRedis(decode_responses=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        acquired = asyncio.Event()
        release = asyncio.Event()
        attempted = asyncio.Event()
        shared_password = "postgres-concurrent-shared-password"
        try:
            async with factory() as reset_session, factory() as setup_session:
                reset_service, reset_context = await _authenticate_runtime(
                    reset_session,
                    redis,
                    population.actor_session_token,
                )
                setup_service = OperatorCredentialSetupService(setup_session)
                _pause_after_department_lock(reset_service.repository, acquired, release)
                _signal_department_lock_attempt(setup_service.repository, attempted)

                reset_task = asyncio.create_task(
                    reset_service.reset_department_password(
                        reset_context,
                        department_id=population.department_id,
                        new_password=shared_password,
                        ip="192.0.2.212",
                        user_agent="postgres-department-reset",
                    )
                )
                await _wait_for_event(acquired, label="Department reset lock")
                setup_task = asyncio.create_task(
                    setup_service.setup(
                        department_id=population.department_id,
                        operator_id=population.legacy_target_id,
                        password=shared_password,
                    )
                )
                await _wait_for_event(attempted, label="legacy setup lock attempt")
                assert not setup_task.done()
                release.set()
                assert await _wait_for_task(reset_task, label="Department reset completion") >= 1
                with pytest.raises(AuthError) as rejected:
                    await _wait_for_task(setup_task, label="legacy setup rejection")
                assert rejected.value.code == "PASSWORD_REUSE_FORBIDDEN"

                row = (
                    await setup_session.execute(
                        text(
                            """
                            SELECT d.password_hash AS department_password_hash,
                                   o.password_hash AS operator_password_hash,
                                   o.credential_version
                            FROM departments AS d
                            JOIN operators AS o ON o.department_id = d.id
                            WHERE d.id = :department_id AND o.id = :operator_id
                            """
                        ),
                        {
                            "department_id": population.department_id,
                            "operator_id": population.legacy_target_id,
                        },
                    )
                ).one()
                assert verify_password(row.department_password_hash, shared_password)
                assert row.operator_password_hash is None
                assert row.credential_version == 0
        finally:
            release.set()
            await redis.aclose()
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("operator_mutation", ("create", "reset"))
def test_operator_password_mutation_serializes_against_department_reset(
    migration_database: MigrationDatabase,
    operator_mutation: str,
) -> None:
    population = _prepare_runtime_population(migration_database)

    async def scenario() -> None:
        engine = create_async_engine(migration_database.engine.url, pool_pre_ping=True)
        redis = FakeRedis(decode_responses=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        acquired = asyncio.Event()
        release = asyncio.Event()
        attempted = asyncio.Event()
        shared_password = f"postgres-{operator_mutation}-wins-shared-password"
        try:
            async with factory() as operator_session, factory() as reset_session:
                _operator_auth, operator_context = await _authenticate_runtime(
                    operator_session,
                    redis,
                    population.actor_session_token,
                )
                reset_service, reset_context = await _authenticate_runtime(
                    reset_session,
                    redis,
                    population.peer_session_token,
                )
                operator_service = OperatorAdminService(operator_session)
                _pause_after_department_lock(operator_service.repository, acquired, release)
                _signal_department_lock_attempt(reset_service.repository, attempted)

                async def mutate_operator() -> None:
                    if operator_mutation == "create":
                        await operator_service.create_operator(
                            operator_context,
                            OperatorAdminCreateInput(
                                name="Concurrent Created Operator",
                                password=shared_password,
                                role="operator",
                                module_grants=[],
                            ),
                            ip="192.0.2.213",
                            user_agent="postgres-operator-create",
                        )
                    else:
                        await operator_service.reset_operator_password(
                            operator_context,
                            operator_id=population.reset_target_id,
                            password=shared_password,
                            ip="192.0.2.213",
                            user_agent="postgres-operator-reset",
                        )

                operator_task = asyncio.create_task(mutate_operator())
                await _wait_for_event(acquired, label=f"Operator {operator_mutation} lock")
                reset_task = asyncio.create_task(
                    reset_service.reset_department_password(
                        reset_context,
                        department_id=population.department_id,
                        new_password=shared_password,
                        ip="192.0.2.214",
                        user_agent="postgres-department-reset-waiter",
                    )
                )
                await _wait_for_event(attempted, label="Department reset lock attempt")
                assert not reset_task.done()
                release.set()
                await _wait_for_task(
                    operator_task,
                    label=f"Operator {operator_mutation} completion",
                )
                with pytest.raises(AuthError) as rejected:
                    await _wait_for_task(reset_task, label="Department reset rejection")
                assert rejected.value.code == "PASSWORD_REUSE_FORBIDDEN"

                async with factory() as verify_session:
                    department_hash = await verify_session.scalar(
                        text("SELECT password_hash FROM departments WHERE id = :id"),
                        {"id": population.department_id},
                    )
                    assert isinstance(department_hash, str)
                    assert verify_password(department_hash, population.department_password)
                    assert not verify_password(department_hash, shared_password)
                    if operator_mutation == "create":
                        operator_hash = await verify_session.scalar(
                            text("SELECT password_hash FROM operators WHERE name = :name"),
                            {"name": "Concurrent Created Operator"},
                        )
                    else:
                        operator_hash = await verify_session.scalar(
                            text("SELECT password_hash FROM operators WHERE id = :id"),
                            {"id": population.reset_target_id},
                        )
                    assert isinstance(operator_hash, str)
                    assert verify_password(operator_hash, shared_password)
        finally:
            release.set()
            await redis.aclose()
            await engine.dispose()

    asyncio.run(scenario())


def test_department_reset_revokes_concurrently_rotated_session(
    migration_database: MigrationDatabase,
) -> None:
    population = _prepare_runtime_population(migration_database)

    async def scenario() -> None:
        engine = create_async_engine(migration_database.engine.url, pool_pre_ping=True)
        redis = FakeRedis(decode_responses=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        acquired = asyncio.Event()
        release = asyncio.Event()
        attempted = asyncio.Event()
        try:
            async with factory() as rotation_session, factory() as reset_session:
                rotation_service, unbound_context = await _authenticate_runtime(
                    rotation_session,
                    redis,
                    population.unbound_session_token,
                )
                reset_service, reset_context = await _authenticate_runtime(
                    reset_session,
                    redis,
                    population.peer_session_token,
                )
                _pause_after_department_lock(rotation_service.repository, acquired, release)
                _signal_department_lock_attempt(reset_service.repository, attempted)

                rotation_task = asyncio.create_task(
                    rotation_service.select_operator(
                        unbound_context,
                        population.actor_id,
                        population.actor_password,
                        ip="192.0.2.215",
                        user_agent="postgres-concurrent-rotation",
                    )
                )
                await _wait_for_event(acquired, label="Operator rotation lock")
                reset_task = asyncio.create_task(
                    reset_service.reset_department_password(
                        reset_context,
                        department_id=population.department_id,
                        new_password="postgres-post-rotation-department-password",
                        ip="192.0.2.216",
                        user_agent="postgres-concurrent-reset",
                    )
                )
                await _wait_for_event(attempted, label="concurrent Department reset lock attempt")
                assert not reset_task.done()
                release.set()
                rotated = await _wait_for_task(rotation_task, label="Operator rotation completion")
                assert (
                    await _wait_for_task(reset_task, label="concurrent Department reset completion")
                    >= 3
                )

                async with factory() as verify_session:
                    verify_service = AuthService(
                        verify_session,
                        RedisLoginThrottle(redis, max_failures=5, lock_seconds=300),
                    )
                    with pytest.raises(AuthError) as rejected:
                        await verify_service.authenticate(rotated.session_token)
                    assert rejected.value.code == "INVALID_SESSION"
                    assert await verify_session.scalar(
                        text(
                            "SELECT revoked_at IS NOT NULL FROM sessions "
                            "WHERE token_hash = :token_hash"
                        ),
                        {"token_hash": hash_token(rotated.session_token)},
                    )
        finally:
            release.set()
            await redis.aclose()
            await engine.dispose()

    asyncio.run(scenario())


def test_target_switch_spray_has_bounded_argon_and_audit_budget_on_postgresql(
    migration_database: MigrationDatabase,
) -> None:
    population = _prepare_runtime_population(migration_database)

    async def scenario() -> None:
        engine = create_async_engine(migration_database.engine.url, pool_pre_ping=True)
        redis = FakeRedis(decode_responses=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                service, context = await _authenticate_runtime(
                    session,
                    redis,
                    population.unbound_session_token,
                )
                for attempt in range(5):
                    with pytest.raises(AuthError) as rejected:
                        await service.select_operator(
                            context,
                            uuid4(),
                            "postgres-sprayed-unknown-password",
                            ip="192.0.2.217",
                            user_agent="postgres-target-switch-spray",
                        )
                    assert rejected.value.code == (
                        "LOGIN_LOCKED" if attempt == 4 else "INVALID_OPERATOR_CREDENTIALS"
                    )
                with pytest.raises(AuthError) as aggregate_locked:
                    await service.select_operator(
                        context,
                        uuid4(),
                        "postgres-sprayed-unknown-password",
                        ip="192.0.2.217",
                        user_agent="postgres-target-switch-spray",
                    )
                assert aggregate_locked.value.code == "LOGIN_LOCKED"
                assert (
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM audit_logs "
                            "WHERE action = 'OPERATOR_AUTH_FAILED'::audit_action"
                        )
                    )
                    == 5
                )
        finally:
            await redis.aclose()
            await engine.dispose()

    asyncio.run(scenario())


def test_operator_auth_audit_actor_target_and_provenance_on_postgresql(
    migration_database: MigrationDatabase,
) -> None:
    population = _prepare_runtime_population(migration_database)

    async def scenario() -> None:
        engine = create_async_engine(migration_database.engine.url, pool_pre_ping=True)
        redis = FakeRedis(decode_responses=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                service, context = await _authenticate_runtime(
                    session,
                    redis,
                    population.unbound_session_token,
                )
                selected_actor = await service.select_operator(
                    context,
                    population.actor_id,
                    population.actor_password,
                    ip="192.0.2.218",
                    user_agent="postgres-current-actor-auth",
                )
                selected_target = await service.select_operator(
                    selected_actor.context,
                    population.reset_target_id,
                    population.reset_target_password,
                    ip="192.0.2.219",
                    user_agent="postgres-current-target-auth",
                )
                audits = (
                    await session.execute(
                        text(
                            """
                            SELECT operator_id, entity_type, entity_id, ip, user_agent
                            FROM audit_logs
                            WHERE action = 'OPERATOR_AUTHENTICATED'::audit_action
                            ORDER BY created_at, id
                            """
                        )
                    )
                ).all()
                assert len(audits) == 2
                assert audits[0].operator_id is None
                assert audits[0].entity_type == "operator"
                assert audits[0].entity_id == population.actor_id
                assert audits[0].ip == "192.0.2.218"
                assert audits[0].user_agent == "postgres-current-actor-auth"
                assert audits[1].operator_id == population.actor_id
                assert audits[1].entity_type == "operator"
                assert audits[1].entity_id == population.reset_target_id
                assert audits[1].ip == "192.0.2.219"
                assert audits[1].user_agent == "postgres-current-target-auth"

                provenance = (
                    await session.execute(
                        text("SELECT ip, user_agent FROM sessions WHERE token_hash = :token_hash"),
                        {"token_hash": hash_token(selected_target.session_token)},
                    )
                ).one()
                assert provenance.ip == "192.0.2.203"
                assert provenance.user_agent == "runtime-unbound-provenance"
        finally:
            await redis.aclose()
            await engine.dispose()

    asyncio.run(scenario())


def _downgrade_with_bounded_database_wait(
    migration_database: MigrationDatabase,
) -> None:
    original_database_url = os.environ["DATABASE_URL"]
    existing_options = migration_database.engine.url.query.get("options")
    assert isinstance(existing_options, str)
    bounded_url = migration_database.engine.url.update_query_dict(
        {
            "options": (
                f"{existing_options} "
                f"-clock_timeout={DOWNGRADE_LOCK_TIMEOUT_MS}ms "
                f"-cstatement_timeout={DOWNGRADE_STATEMENT_TIMEOUT_MS}ms"
            )
        }
    )
    try:
        os.environ["DATABASE_URL"] = bounded_url.render_as_string(hide_password=False).replace(
            "%", "%%"
        )
        get_settings.cache_clear()
        migration_database.downgrade(PERMISSIONS_REVISION)
    finally:
        os.environ["DATABASE_URL"] = original_database_url
        get_settings.cache_clear()


def test_downgrade_lock_observes_concurrent_credential_initialization(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade(PERMISSIONS_REVISION)
    with migration_database.engine.begin() as connection:
        seeded = _seed_legacy_bound_operator(connection)
    migration_database.upgrade(OPERATOR_AUTH_REVISION)

    writer = migration_database.engine.connect()
    writer_transaction = writer.begin()
    writer.execute(
        text(
            """
            UPDATE operators
            SET password_hash = :password_hash, credential_version = 1
            WHERE id = :operator_id
            """
        ),
        {
            "operator_id": seeded["operator_id"],
            "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$concurrent-test-hash",
        },
    )
    executor = ThreadPoolExecutor(max_workers=1)
    downgrade = executor.submit(_downgrade_with_bounded_database_wait, migration_database)
    try:
        deadline = time.monotonic() + DOWNGRADE_LOCK_OBSERVATION_TIMEOUT_SECONDS
        lock_wait_observed = False
        while time.monotonic() < deadline:
            with migration_database.engine.connect() as observer:
                lock_wait_observed = bool(
                    observer.scalar(
                        text(
                            """
                            SELECT EXISTS (
                                SELECT 1
                                FROM pg_locks
                                WHERE relation = to_regclass('operators')
                                  AND mode = 'AccessExclusiveLock'
                                  AND NOT granted
                            )
                            """
                        )
                    )
                )
            if lock_wait_observed:
                break
            if downgrade.done():
                downgrade.result(timeout=0)
            time.sleep(0.01)
        assert lock_wait_observed, "downgrade never waited on the credential writer"
        writer_transaction.commit()
        with pytest.raises(RuntimeError, match="initialized Operator credentials exist"):
            downgrade.result(timeout=DOWNGRADE_FUTURE_TIMEOUT_SECONDS)
    finally:
        if writer_transaction.is_active:
            writer_transaction.rollback()
        writer.close()
        try:
            downgrade.result(timeout=DOWNGRADE_FUTURE_TIMEOUT_SECONDS)
        except FutureTimeoutError as error:
            raise AssertionError(
                "bounded PostgreSQL downgrade did not terminate after writer release"
            ) from error
        except Exception:
            # The main assertion owns the exact expected error. Cleanup only
            # proves that the database operation itself has terminated.
            pass
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    with migration_database.engine.connect() as connection:
        assert _revision(connection) == OPERATOR_AUTH_REVISION
        assert "password_hash" in _column_names(connection, "operators")
        assert (
            connection.scalar(
                text("SELECT credential_version FROM operators WHERE id = :operator_id"),
                {"operator_id": seeded["operator_id"]},
            )
            == 1
        )
