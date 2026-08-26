"""PostgreSQL 16 gates for ``0005_phase2_refresh_queue``.

The tests require an explicitly named disposable PostgreSQL database and put
every run in a random schema.  They verify both upgrade paths, physical
constraints/indexes, ORM drift, and the data-preserving downgrade guard.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from backend_core.config import get_settings
from sqlalchemy import Connection, Engine, create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, DBAPIError
from sqlalchemy.schema import CreateSchema, DropSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "infrastructure" / "migrations" / "alembic.ini"
MIGRATIONS = PROJECT_ROOT / "infrastructure" / "migrations"


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

    def check(self) -> None:
        get_settings.cache_clear()
        command.check(self.config)


@pytest.fixture
def migration_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[MigrationDatabase]:
    schema_name = f"phase2_refresh_migration_{uuid4().hex}"
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
    version_number = int(connection.scalar(text("SHOW server_version_num")))
    assert version_number // 10_000 == 16


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


def _seed_0004_graph(connection: Connection) -> dict[str, UUID]:
    department_id = uuid4()
    operator_id = uuid4()
    collection_id = uuid4()
    stored_file_id = uuid4()
    import_job_id = uuid4()
    import_file_id = uuid4()
    import_row_id = uuid4()
    influencer_id = uuid4()
    account_id = uuid4()
    source_state_id = uuid4()
    metrics_id = uuid4()
    observed_at = datetime(2026, 8, 1, tzinfo=UTC)
    source_updated_at = datetime(2026, 7, 1, tzinfo=UTC)

    connection.execute(
        text(
            """
            INSERT INTO departments (id, name, password_hash, status, session_days)
            VALUES (:id, :name, 'not-a-hash', 'active'::department_status, 30)
            """
        ),
        {"id": department_id, "name": f"Task 8 migration {uuid4().hex}"},
    )
    connection.execute(
        text(
            """
            INSERT INTO operators (id, department_id, name, role, status)
            VALUES (
                :id, :department_id, 'Task 8 operator',
                'operator'::role_enum, 'active'::operator_status
            )
            """
        ),
        {"id": operator_id, "department_id": department_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO collection_jobs (
                id, name, industry, purpose, target_action, target_count,
                department_id, owner_operator_id, source_type, status,
                screening_rules, screening_rules_revision
            ) VALUES (
                :id, 'Task 8 source', 'synthetic', 'migration evidence',
                'refresh', 1, :department_id, :operator_id,
                'manual_huitun_export'::import_source_type,
                'completed'::collection_job_status,
                CAST(:screening_rules AS jsonb),
                1
            )
            """
        ),
        {
            "id": collection_id,
            "department_id": department_id,
            "operator_id": operator_id,
            "screening_rules": json.dumps(
                {
                    "schema_version": 1,
                    "platforms": [],
                    "source_tags_exact_any": [],
                }
            ),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO stored_import_files (
                id, sha256, storage_key, size, detected_type,
                detected_mime, encoding, expires_at
            ) VALUES (
                :id, :sha256, :storage_key, 1, 'csv'::stored_file_type,
                'text/csv', 'utf-8', :expires_at
            )
            """
        ),
        {
            "id": stored_file_id,
            "sha256": uuid4().hex * 2,
            "storage_key": f"task8/{uuid4().hex}.csv",
            "expires_at": observed_at + timedelta(days=30),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO import_jobs (
                id, collection_job_id, department_id, operator_id, source_type,
                status, preview_revision, confirmed_revision, confirmed_at,
                completed_at
            ) VALUES (
                :id, :collection_id, :department_id, :operator_id,
                'manual_huitun_export'::import_source_type,
                'completed'::import_job_status, 1, 1, :observed_at, :observed_at
            )
            """
        ),
        {
            "id": import_job_id,
            "collection_id": collection_id,
            "department_id": department_id,
            "operator_id": operator_id,
            "observed_at": observed_at,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO import_job_files (
                id, import_job_id, stored_file_id, position, original_filename,
                declared_mime, status, source_acquired_at,
                source_acquired_at_origin, source_acquired_at_confirmation_required
            ) VALUES (
                :id, :job_id, :stored_file_id, 1, 'task8.csv', 'text/csv',
                'ready'::import_job_file_status, :observed_at,
                'user_confirmed'::source_acquired_at_origin, false
            )
            """
        ),
        {
            "id": import_file_id,
            "job_id": import_job_id,
            "stored_file_id": stored_file_id,
            "observed_at": observed_at,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO influencers (id, display_name, crm_stage, status)
            VALUES (
                :id, 'Task 8 candidate', '待开发'::crm_stage,
                'active'::influencer_status
            )
            """
        ),
        {"id": influencer_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO influencer_platform_accounts (
                id, influencer_id, platform, platform_account_id,
                account_name, account_handle, profile_url,
                normalized_profile_url, source, is_active
            ) VALUES (
                :id, :influencer_id, 'xiaohongshu'::platform_enum,
                'migration-account', 'Migration Account', 'migration-handle',
                'https://example.invalid/migration-account',
                'https://example.invalid/migration-account',
                'huitun'::data_source, true
            )
            """
        ),
        {"id": account_id, "influencer_id": influencer_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO import_rows (
                id, import_job_id, import_job_file_id, row_number,
                raw_data, normalized_data, matched_influencer_id,
                matched_platform_account_id, match_type, action,
                warnings, errors, preview_revision, plan_hash,
                committed_action, committed_at
            ) VALUES (
                :id, :job_id, :file_id, 2, '{}'::jsonb, '{}'::jsonb,
                :influencer_id, :account_id, 'platform_account_id'::import_match_type,
                'no_change'::import_row_action, '[]'::jsonb, '[]'::jsonb,
                1, :plan_hash, 'no_change'::import_row_action, :observed_at
            )
            """
        ),
        {
            "id": import_row_id,
            "job_id": import_job_id,
            "file_id": import_file_id,
            "influencer_id": influencer_id,
            "account_id": account_id,
            "plan_hash": uuid4().hex * 2,
            "observed_at": observed_at,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO influencer_source_states (
                id, influencer_id, platform_account_id, source,
                source_updated_at, source_data, source_data_hash, state_version,
                last_import_job_id, last_import_row_id
            ) VALUES (
                :id, :influencer_id, :account_id, 'huitun'::data_source,
                :source_updated_at, '{}'::jsonb, :hash, 1, :job_id, :row_id
            )
            """
        ),
        {
            "id": source_state_id,
            "influencer_id": influencer_id,
            "account_id": account_id,
            "source_updated_at": source_updated_at,
            "hash": uuid4().hex * 2,
            "job_id": import_job_id,
            "row_id": import_row_id,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO influencer_current_metrics (
                id, influencer_id, platform_account_id, source,
                source_updated_at, metrics, metrics_hash,
                last_import_job_id, last_import_row_id
            ) VALUES (
                :id, :influencer_id, :account_id, 'huitun'::data_source,
                :source_updated_at, CAST(:metrics AS jsonb),
                :hash, :job_id, :row_id
            )
            """
        ),
        {
            "id": metrics_id,
            "influencer_id": influencer_id,
            "account_id": account_id,
            "source_updated_at": source_updated_at,
            "metrics": json.dumps({"followers_count": 1234}),
            "hash": uuid4().hex * 2,
            "job_id": import_job_id,
            "row_id": import_row_id,
        },
    )
    return {
        "department_id": department_id,
        "operator_id": operator_id,
        "import_job_id": import_job_id,
        "import_row_id": import_row_id,
        "influencer_id": influencer_id,
        "account_id": account_id,
        "source_state_id": source_state_id,
        "metrics_id": metrics_id,
    }


def _assert_0004_graph(connection: Connection, seeded: dict[str, UUID]) -> None:
    checks = (
        ("departments", seeded["department_id"]),
        ("operators", seeded["operator_id"]),
        ("import_jobs", seeded["import_job_id"]),
        ("import_rows", seeded["import_row_id"]),
        ("influencers", seeded["influencer_id"]),
        ("influencer_platform_accounts", seeded["account_id"]),
        ("influencer_source_states", seeded["source_state_id"]),
        ("influencer_current_metrics", seeded["metrics_id"]),
    )
    for table_name, record_id in checks:
        assert (
            connection.scalar(
                text(f"SELECT count(*) FROM {table_name} WHERE id = :id"),
                {"id": record_id},
            )
            == 1
        )


def _assert_database_rejects(
    connection: Connection,
    statement: str,
    parameters: dict[str, Any],
) -> None:
    with pytest.raises(DBAPIError):
        with connection.begin_nested():
            connection.execute(text(statement), parameters)


def _insert_queue(
    connection: Connection,
    seeded: dict[str, UUID],
    *,
    department_id: UUID | None = None,
) -> UUID:
    queue_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO refresh_queues (
                id, department_id, created_by_operator_id, status, as_of,
                requested_limit, today_total_limit, refresh_limit,
                policy_version, criteria_snapshot
            ) VALUES (
                :id, :department_id, :operator_id, 'open'::refresh_queue_status,
                :as_of, 1, 200, 100, 1, CAST(:criteria AS jsonb)
            )
            """
        ),
        {
            "id": queue_id,
            "department_id": department_id or seeded["department_id"],
            "operator_id": seeded["operator_id"],
            "as_of": datetime(2026, 8, 13, tzinfo=UTC),
            "criteria": json.dumps({"schema_version": 1}),
        },
    )
    return queue_id


def _insert_item(
    connection: Connection,
    seeded: dict[str, UUID],
    *,
    queue_id: UUID,
    department_id: UUID | None = None,
    status: str = "pending",
) -> UUID:
    item_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO refresh_queue_items (
                id, department_id, queue_id, influencer_id,
                platform_account_id, source, priority_tier,
                priority_reasons, identity_snapshot,
                baseline_last_observed_at, baseline_source_updated_at, status
            ) VALUES (
                :id, :department_id, :queue_id, :influencer_id,
                :account_id, 'huitun'::data_source, 2,
                '["VERY_STALE"]'::jsonb, CAST(:identity AS jsonb),
                :observed_at, :source_updated_at,
                CAST(:status AS refresh_queue_item_status)
            )
            """
        ),
        {
            "id": item_id,
            "department_id": department_id or seeded["department_id"],
            "queue_id": queue_id,
            "influencer_id": seeded["influencer_id"],
            "account_id": seeded["account_id"],
            "identity": json.dumps(
                {
                    "schema_version": 1,
                    "platform": "xiaohongshu",
                    "account_name": "Migration Account",
                    "platform_account_id": "migration-account",
                    "followers_count": 1234,
                }
            ),
            "observed_at": datetime(2026, 8, 1, tzinfo=UTC),
            "source_updated_at": datetime(2026, 7, 1, tzinfo=UTC),
            "status": status,
        },
    )
    return item_id


def test_fresh_upgrade_repeat_metadata_and_alembic_check(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("head")
    migration_database.upgrade("head")
    migration_database.check()

    with migration_database.engine.connect() as connection:
        _assert_postgresql_16(connection)
        assert _revision(connection) == "0008_content_activity_p0"
        inspector = inspect(connection)
        assert inspector.has_table("refresh_queues")
        assert inspector.has_table("refresh_queue_items")
        assert "refresh_queue_id" in {
            column["name"] for column in inspector.get_columns("import_jobs")
        }
        assert _enum_values(connection, "refresh_queue_status") == [
            "open",
            "exported",
            "completed",
            "cancelled",
        ]
        assert _enum_values(connection, "refresh_queue_item_status") == [
            "pending",
            "fulfilled_changed",
            "fulfilled_no_change",
            "stale_return",
            "unresolved",
            "cancelled",
        ]
        audit_values = set(_enum_values(connection, "audit_action"))
        assert {
            "REFRESH_QUEUE_CREATED",
            "REFRESH_QUEUE_EXPORTED",
            "REFRESH_QUEUE_CANCELLED",
        } <= audit_values

        queue_checks = {
            constraint["name"] for constraint in inspector.get_check_constraints("refresh_queues")
        }
        assert {
            "ck_refresh_queue_limit_order",
            "ck_refresh_queue_limits_positive",
            "ck_refresh_queue_policy_version",
            "ck_refresh_queue_requested_limit_max",
            "ck_refresh_queue_status_timestamps",
        } <= queue_checks
        item_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("refresh_queue_items")
        }
        assert {
            "ck_refresh_queue_item_fulfillment_pair",
            "ck_refresh_queue_item_fulfillment_state",
            "ck_refresh_queue_item_last_return_pair",
            "ck_refresh_queue_item_priority_tier",
            "ck_refresh_queue_item_source_huitun",
        } <= item_checks
        index_definitions = {
            row["indexname"]: row["indexdef"]
            for row in connection.execute(
                text(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND tablename IN ('refresh_queues', 'refresh_queue_items')
                    """
                )
            ).mappings()
        }
        assert {
            "ix_refresh_queues_department_status_created",
            "ix_refresh_queue_items_queue_priority",
            "ix_refresh_queue_items_queue_status",
            "uq_refresh_queue_item_active_candidate",
        } <= set(index_definitions)
        active_definition = index_definitions["uq_refresh_queue_item_active_candidate"]
        assert "UNIQUE INDEX" in active_definition
        for status in ("pending", "stale_return", "unresolved"):
            assert status in active_definition


def test_0004_realistic_data_upgrades_without_mutating_core_lineage(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("0004_phase2_bulk_import")
    with migration_database.engine.begin() as connection:
        seeded = _seed_0004_graph(connection)

    migration_database.upgrade("0005_phase2_refresh_queue")
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0005_phase2_refresh_queue"
        _assert_0004_graph(connection, seeded)
        assert connection.scalar(text("SELECT count(*) FROM refresh_queues")) == 0
        assert connection.scalar(text("SELECT count(*) FROM refresh_queue_items")) == 0
        assert (
            connection.scalar(
                text("SELECT refresh_queue_id FROM import_jobs WHERE id = :id"),
                {"id": seeded["import_job_id"]},
            )
            is None
        )


def test_safe_empty_downgrade_and_reupgrade_preserve_0004_graph(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("0004_phase2_bulk_import")
    with migration_database.engine.begin() as connection:
        seeded = _seed_0004_graph(connection)
    migration_database.upgrade("0005_phase2_refresh_queue")
    migration_database.downgrade("0004_phase2_bulk_import")

    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0004_phase2_bulk_import"
        _assert_0004_graph(connection, seeded)
        assert not inspect(connection).has_table("refresh_queues")
        assert "refresh_queue_id" not in {
            column["name"] for column in inspect(connection).get_columns("import_jobs")
        }

    migration_database.upgrade("0005_phase2_refresh_queue")
    with migration_database.engine.connect() as connection:
        _assert_0004_graph(connection, seeded)
        assert _revision(connection) == "0005_phase2_refresh_queue"


def test_constraints_enforce_department_account_source_and_status_invariants(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("head")
    with migration_database.engine.begin() as connection:
        seeded = _seed_0004_graph(connection)
        other_department = uuid4()
        connection.execute(
            text(
                """
                INSERT INTO departments (id, name, password_hash, status, session_days)
                VALUES (:id, :name, 'not-a-hash', 'active'::department_status, 30)
                """
            ),
            {"id": other_department, "name": f"Task 8 other {uuid4().hex}"},
        )
        queue_id = _insert_queue(connection, seeded)
        _insert_item(connection, seeded, queue_id=queue_id)

        duplicate_queue_id = _insert_queue(connection, seeded)
        _assert_database_rejects(
            connection,
            """
            INSERT INTO refresh_queue_items (
                id, department_id, queue_id, influencer_id,
                platform_account_id, source, priority_tier,
                priority_reasons, identity_snapshot, status
            ) VALUES (
                :id, :department_id, :queue_id, :influencer_id, :account_id,
                'huitun'::data_source, 2, '["VERY_STALE"]'::jsonb,
                CAST(:identity AS jsonb),
                'pending'::refresh_queue_item_status
            )
            """,
            {
                "id": uuid4(),
                "department_id": seeded["department_id"],
                "queue_id": duplicate_queue_id,
                "influencer_id": seeded["influencer_id"],
                "account_id": seeded["account_id"],
                "identity": json.dumps(
                    {
                        "schema_version": 1,
                        "platform": "xiaohongshu",
                        "account_name": "candidate",
                    }
                ),
            },
        )
        _assert_database_rejects(
            connection,
            """
            INSERT INTO refresh_queue_items (
                id, department_id, queue_id, influencer_id,
                platform_account_id, source, priority_tier,
                priority_reasons, identity_snapshot, status
            ) VALUES (
                :id, :other_department, :queue_id, :influencer_id, :account_id,
                'huitun'::data_source, 2, '["VERY_STALE"]'::jsonb,
                '{}'::jsonb, 'pending'::refresh_queue_item_status
            )
            """,
            {
                "id": uuid4(),
                "other_department": other_department,
                "queue_id": queue_id,
                "influencer_id": seeded["influencer_id"],
                "account_id": seeded["account_id"],
            },
        )
        _assert_database_rejects(
            connection,
            """
            INSERT INTO refresh_queue_items (
                id, department_id, queue_id, influencer_id,
                platform_account_id, source, priority_tier,
                priority_reasons, identity_snapshot, status
            ) VALUES (
                :id, :department_id, :queue_id, :wrong_influencer, :account_id,
                'huitun'::data_source, 2, '["VERY_STALE"]'::jsonb,
                '{}'::jsonb, 'cancelled'::refresh_queue_item_status
            )
            """,
            {
                "id": uuid4(),
                "department_id": seeded["department_id"],
                "queue_id": queue_id,
                "wrong_influencer": uuid4(),
                "account_id": seeded["account_id"],
            },
        )
        _assert_database_rejects(
            connection,
            "UPDATE refresh_queue_items SET source = 'generic'::data_source WHERE queue_id = :id",
            {"id": queue_id},
        )
        _assert_database_rejects(
            connection,
            """
            UPDATE refresh_queue_items
            SET status = 'fulfilled_changed'::refresh_queue_item_status
            WHERE queue_id = :id
            """,
            {"id": queue_id},
        )
        _assert_database_rejects(
            connection,
            "UPDATE refresh_queues SET requested_limit = 2001 WHERE id = :id",
            {"id": queue_id},
        )
        _assert_database_rejects(
            connection,
            "UPDATE refresh_queues SET status = 'exported'::refresh_queue_status WHERE id = :id",
            {"id": queue_id},
        )


@pytest.mark.parametrize("unsafe_kind", ("queue", "import-reference"))
def test_downgrade_refuses_queue_evidence_or_import_reference_and_preserves_core_data(
    migration_database: MigrationDatabase,
    unsafe_kind: str,
) -> None:
    migration_database.upgrade("0005_phase2_refresh_queue")
    with migration_database.engine.begin() as connection:
        seeded = _seed_0004_graph(connection)
        queue_id = _insert_queue(connection, seeded)
        if unsafe_kind == "import-reference":
            connection.execute(
                text("UPDATE import_jobs SET refresh_queue_id = :queue_id WHERE id = :job_id"),
                {"queue_id": queue_id, "job_id": seeded["import_job_id"]},
            )

    with pytest.raises(RuntimeError, match="0005 downgrade blocked"):
        migration_database.downgrade("0004_phase2_bulk_import")

    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0005_phase2_refresh_queue"
        _assert_0004_graph(connection, seeded)
        assert (
            connection.scalar(
                text("SELECT count(*) FROM refresh_queues WHERE id = :id"),
                {"id": queue_id},
            )
            == 1
        )
