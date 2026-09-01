"""PostgreSQL 16 composition gate for the Buyer V1 production migration.

The target must be the explicitly disposable ``phase1b_test`` database. Each
test uses an isolated random schema and removes it after verification.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from backend_core.config import get_settings
from sqlalchemy import Connection, Engine, create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.schema import CreateSchema, DropSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "infrastructure" / "migrations" / "alembic.ini"
MIGRATIONS = PROJECT_ROOT / "infrastructure" / "migrations"
OPERATOR_AUTH_REVISION = "0011_operator_auth_p0"
BUYER_REVISION = "0012_buyer_lead_tiers_v1"
BUYER_LEAD_TIER_VALUES = ["HIGH", "CHANGED", "RELATED", "SAME_CATEGORY", "UNKNOWN"]


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


@pytest.fixture
def migration_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[MigrationDatabase]:
    schema_name = f"buyer_v1_migration_{uuid4().hex}"
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


def _assert_composed_schema(migration_database: MigrationDatabase) -> None:
    with migration_database.engine.connect() as connection:
        _assert_postgresql_16(connection)
        assert _revision(connection) == BUYER_REVISION
        assert ScriptDirectory.from_config(migration_database.config).get_heads() == [
            BUYER_REVISION
        ]

        inspector = inspect(connection)
        member_columns = {
            column["name"] for column in inspector.get_columns("candidate_pool_members")
        }
        assert {"buyer_lead_tier", "buyer_relation_summary"} <= member_columns
        assert "ix_candidate_pool_members_run_buyer_tier" in {
            index["name"] for index in inspector.get_indexes("candidate_pool_members")
        }
        assert _enum_values(connection, "buyer_lead_tier") == BUYER_LEAD_TIER_VALUES
        assert {"MATCH", "UNKNOWN", "NOT_MATCH"} <= set(
            _enum_values(connection, "candidate_result")
        )
        assert connection.scalar(text("SELECT 'NOT_MATCH'::candidate_result::text")) == "NOT_MATCH"

        operator_columns = {column["name"] for column in inspector.get_columns("operators")}
        session_columns = {column["name"] for column in inspector.get_columns("sessions")}
        assert {"password_hash", "credential_version"} <= operator_columns
        assert "operator_credential_version" in session_columns


def test_upgrade_from_operator_auth_to_buyer_head(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade(OPERATOR_AUTH_REVISION)
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == OPERATOR_AUTH_REVISION
        assert "password_hash" in {
            column["name"] for column in inspect(connection).get_columns("operators")
        }

    migration_database.upgrade(BUYER_REVISION)
    migration_database.upgrade("head")
    _assert_composed_schema(migration_database)


def test_fresh_full_chain_reaches_single_buyer_head(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("head")
    _assert_composed_schema(migration_database)
