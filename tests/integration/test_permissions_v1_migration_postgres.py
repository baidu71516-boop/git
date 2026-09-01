"""PostgreSQL 16-only gate for Permissions V1 Task 2 persistence.

The target must be the explicitly disposable ``phase1b_test`` database.  Each
case receives a random schema, upgrades through the frozen Douyin 0009 parent,
and drops its synthetic data at teardown.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from backend_core.config import get_settings
from sqlalchemy import Connection, Engine, create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "infrastructure" / "migrations" / "alembic.ini"
MIGRATIONS = PROJECT_ROOT / "infrastructure" / "migrations"
DOUYIN_REVISION = "0009_douyin_runtime_capture_v1"
PERMISSIONS_REVISION = "0010_permissions_v1_persistence"
NON_ADMIN_MODULES = (
    "today_outreach",
    "campaigns",
    "candidate_pools",
    "influencer_library",
    "data_collection",
    "import_history",
    "data_updates",
)
PERMISSIONS_AUDIT_ACTIONS = (
    "OPERATOR_CREATED",
    "OPERATOR_UPDATED",
    "OPERATOR_ROLE_CHANGED",
    "OPERATOR_STATUS_CHANGED",
    "OPERATOR_MODULE_GRANTS_CHANGED",
)


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


@pytest.fixture
def migration_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[MigrationDatabase]:
    schema_name = f"permissions_v1_migration_{uuid4().hex}"
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


def _seed_operator(
    connection: Connection,
    *,
    department_role: str,
    operator_role: str,
    status: str = "active",
) -> tuple[UUID, UUID]:
    department_id = uuid4()
    operator_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO departments (id, name, password_hash, status, session_days)
            VALUES (:id, :name, 'migration-only-password-hash', 'active'::department_status, 30)
            """
        ),
        {"id": department_id, "name": f"Permissions migration {department_id}"},
    )
    connection.execute(
        text(
            """
            INSERT INTO department_permissions (id, department_id, role)
            VALUES (:id, :department_id, CAST(:role AS role_enum))
            """
        ),
        {"id": uuid4(), "department_id": department_id, "role": department_role},
    )
    connection.execute(
        text(
            """
            INSERT INTO operators (id, department_id, name, role, status)
            VALUES (:id, :department_id, :name, CAST(:role AS role_enum),
                    CAST(:status AS operator_status))
            """
        ),
        {
            "id": operator_id,
            "department_id": department_id,
            "name": f"Operator {operator_id}",
            "role": operator_role,
            "status": status,
        },
    )
    return department_id, operator_id


def _seed_valid_population(connection: Connection) -> dict[str, tuple[UUID, UUID]]:
    return {
        "non_super_admin": _seed_operator(
            connection,
            department_role="operator",
            operator_role="operator",
        ),
        "super_admin": _seed_operator(
            connection,
            department_role="super_admin",
            operator_role="super_admin",
        ),
        "disabled": _seed_operator(
            connection,
            department_role="manager",
            operator_role="manager",
            status="disabled",
        ),
    }


def test_upgrade_backfill_constraints_audit_and_single_head(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade(DOUYIN_REVISION)
    with migration_database.engine.begin() as connection:
        seeded = _seed_valid_population(connection)

    migration_database.upgrade("head")
    migration_database.upgrade("head")
    with migration_database.engine.connect() as connection:
        _assert_postgresql_16(connection)
        assert _revision(connection) == PERMISSIONS_REVISION
        assert ScriptDirectory.from_config(migration_database.config).get_heads() == [
            PERMISSIONS_REVISION
        ]
        assert inspect(connection).has_table("operator_module_permissions")
        non_super_admin_department, non_super_admin = seeded["non_super_admin"]
        super_admin_department, super_admin = seeded["super_admin"]
        disabled_department, disabled = seeded["disabled"]
        assert connection.scalar(
            text(
                "SELECT count(*) FROM operator_module_permissions WHERE operator_id = :operator_id"
            ),
            {"operator_id": non_super_admin},
        ) == 7
        assert tuple(
            connection.scalars(
                text(
                    """
                    SELECT module_key
                    FROM operator_module_permissions
                    WHERE operator_id = :operator_id
                    ORDER BY module_key
                    """
                ),
                {"operator_id": non_super_admin},
            )
        ) == tuple(sorted(NON_ADMIN_MODULES))
        assert connection.scalar(
            text(
                "SELECT count(*) FROM operator_module_permissions WHERE operator_id IN "
                "(:super_admin, :disabled)"
            ),
            {"super_admin": super_admin, "disabled": disabled},
        ) == 0
        assert non_super_admin_department != super_admin_department != disabled_department
        assert set(PERMISSIONS_AUDIT_ACTIONS) <= set(_enum_values(connection, "audit_action"))

    with pytest.raises(IntegrityError):
        with migration_database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO operator_module_permissions (operator_id, department_id, module_key)
                    VALUES (:operator_id, :department_id, 'admin')
                    """
                ),
                {"operator_id": non_super_admin, "department_id": super_admin_department},
            )
    with pytest.raises(IntegrityError):
        with migration_database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO operator_module_permissions (operator_id, department_id, module_key)
                    VALUES (:operator_id, :department_id, 'campaigns')
                    """
                ),
                {"operator_id": non_super_admin, "department_id": non_super_admin_department},
            )
    with pytest.raises(IntegrityError):
        with migration_database.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO operator_module_permissions (operator_id, department_id, module_key)
                    VALUES (:operator_id, :department_id, 'unknown_module')
                    """
                ),
                {"operator_id": non_super_admin, "department_id": non_super_admin_department},
            )


def test_preflight_blocks_above_ceiling_without_schema_residue(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade(DOUYIN_REVISION)
    with migration_database.engine.begin() as connection:
        _seed_operator(
            connection,
            department_role="operator",
            operator_role="super_admin",
        )

    with pytest.raises(RuntimeError, match="exceeds Department ceiling"):
        migration_database.upgrade(PERMISSIONS_REVISION)
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == DOUYIN_REVISION
        assert not inspect(connection).has_table("operator_module_permissions")
        assert not set(PERMISSIONS_AUDIT_ACTIONS) <= set(_enum_values(connection, "audit_action"))


def test_preflight_blocks_unapproved_write_loss_without_schema_residue(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade(DOUYIN_REVISION)
    with migration_database.engine.begin() as connection:
        _seed_operator(
            connection,
            department_role="operator",
            operator_role="viewer",
        )

    with pytest.raises(RuntimeError, match="would lose legacy write capability"):
        migration_database.upgrade(PERMISSIONS_REVISION)
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == DOUYIN_REVISION
        assert not inspect(connection).has_table("operator_module_permissions")


def test_preflight_blocks_unknown_role_without_schema_residue(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade(DOUYIN_REVISION)
    with migration_database.engine.begin() as connection:
        _, operator_id = _seed_operator(
            connection,
            department_role="operator",
            operator_role="operator",
        )
    with migration_database.engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        connection.execute(text("ALTER TYPE role_enum ADD VALUE IF NOT EXISTS 'legacy_unknown'"))
        connection.execute(
            text("UPDATE operators SET role = 'legacy_unknown' WHERE id = :operator_id"),
            {"operator_id": operator_id},
        )

    with pytest.raises(RuntimeError, match="unknown role"):
        migration_database.upgrade(PERMISSIONS_REVISION)
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == DOUYIN_REVISION
        assert not inspect(connection).has_table("operator_module_permissions")


def test_downgrade_preserves_0009_douyin_schema_and_safe_reupgrade(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade(DOUYIN_REVISION)
    with migration_database.engine.begin() as connection:
        seeded = _seed_valid_population(connection)
    migration_database.upgrade(PERMISSIONS_REVISION)
    migration_database.downgrade(DOUYIN_REVISION)

    with migration_database.engine.connect() as connection:
        assert _revision(connection) == DOUYIN_REVISION
        assert not inspect(connection).has_table("operator_module_permissions")
        assert "douyin" in _enum_values(connection, "platform_enum")
        assert "HUITUN_DOUYIN_AWEME_LIST" in _enum_values(
            connection, "content_activity_provider"
        )
        assert "capture_token_digest" in {
            column["name"]
            for column in inspect(connection).get_columns("content_activity_refresh_requests")
        }
        assert set(PERMISSIONS_AUDIT_ACTIONS) <= set(_enum_values(connection, "audit_action"))

    migration_database.upgrade(PERMISSIONS_REVISION)
    with migration_database.engine.connect() as connection:
        non_super_admin = seeded["non_super_admin"][1]
        assert _revision(connection) == PERMISSIONS_REVISION
        assert connection.scalar(
            text(
                "SELECT count(*) FROM operator_module_permissions WHERE operator_id = :operator_id"
            ),
            {"operator_id": non_super_admin},
        ) == 7
