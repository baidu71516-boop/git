"""PostgreSQL 16 gates for the Phase 2 bulk-import migration.

These tests intentionally require an explicitly gated disposable database.  Every
test creates an isolated schema and drops it afterwards; no development or
production database is accepted.
"""

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
DEFAULT_SCREENING_RULES = {
    "schema_version": 1,
    "platforms": [],
    "source_tags_exact_any": [],
}


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
    """Alembic command wrapper scoped to one disposable PostgreSQL schema."""

    def __init__(self, engine: Engine, config: Config, schema_name: str) -> None:
        self.engine = engine
        self.config = config
        self.schema_name = schema_name

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
    schema_name = f"phase2_bulk_migration_{uuid4().hex}"
    admin_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(CreateSchema(schema_name))

    scoped_url = TEST_DATABASE_URL.update_query_dict({"options": f"-csearch_path={schema_name}"})
    # Alembic's env.py writes this value through ConfigParser.  Escaping percent
    # signs preserves URL-encoded libpq options (notably ``%3D``) without
    # triggering ConfigParser interpolation.
    database_url = scoped_url.render_as_string(hide_password=False).replace("%", "%%")
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS))
    engine = create_engine(scoped_url, pool_pre_ping=True)
    database = MigrationDatabase(engine, config, schema_name)
    try:
        yield database
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


def _seed_confirmed_lineage(
    connection: Connection,
    *,
    job_id: UUID,
    row_id: UUID,
    operator_id: UUID,
) -> dict[str, UUID]:
    influencer_id = uuid4()
    account_id = uuid4()
    source_state_id = uuid4()
    contact_id = uuid4()
    metrics_id = uuid4()
    snapshot_id = uuid4()
    source_identity_id = uuid4()
    observed_at = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)

    connection.execute(
        text(
            """
            INSERT INTO influencers (
                id, display_name, owner_operator_id, crm_stage, status
            ) VALUES (
                :id, '迁移已确认达人', :operator_id,
                '待开发'::crm_stage, 'active'::influencer_status
            )
            """
        ),
        {"id": influencer_id, "operator_id": operator_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO influencer_platform_accounts (
                id, influencer_id, platform, platform_account_id,
                account_name, account_handle, profile_url,
                normalized_profile_url, source, source_tags
            ) VALUES (
                :id, :influencer_id, 'xiaohongshu'::platform_enum,
                'migration-confirmed-account', '迁移账号', 'migration-handle',
                'https://example.invalid/profile/migration-confirmed-account',
                'https://example.invalid/profile/migration-confirmed-account',
                'huitun'::data_source, '["动画"]'::jsonb
            )
            """
        ),
        {"id": account_id, "influencer_id": influencer_id},
    )
    connection.execute(
        text(
            """
            UPDATE import_rows
            SET matched_influencer_id = :influencer_id,
                matched_platform_account_id = :account_id,
                committed_action = 'create'::import_row_action,
                committed_at = :observed_at
            WHERE id = :row_id AND import_job_id = :job_id
            """
        ),
        {
            "influencer_id": influencer_id,
            "account_id": account_id,
            "observed_at": observed_at,
            "row_id": row_id,
            "job_id": job_id,
        },
    )
    connection.execute(
        text(
            """
            UPDATE import_jobs
            SET confirmed_revision = 1,
                confirm_task_id = 'legacy-confirm-task',
                confirmed_at = :observed_at,
                completed_at = :observed_at,
                created_rows = 1,
                result = CAST(:result AS jsonb)
            WHERE id = :job_id
            """
        ),
        {
            "observed_at": observed_at,
            "job_id": job_id,
            "result": json.dumps({"created": 1}),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO influencer_source_states (
                id, influencer_id, platform_account_id, source,
                source_updated_at, source_data, source_data_hash,
                state_version, last_import_job_id, last_import_row_id
            ) VALUES (
                :id, :influencer_id, :account_id, 'huitun'::data_source,
                :observed_at, CAST(:source_data AS jsonb), :hash,
                1, :job_id, :row_id
            )
            """
        ),
        {
            "id": source_state_id,
            "influencer_id": influencer_id,
            "account_id": account_id,
            "observed_at": observed_at,
            "source_data": json.dumps({"creator_tags": ["动画"]}),
            "hash": "d" * 64,
            "job_id": job_id,
            "row_id": row_id,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO influencer_contacts (
                id, influencer_id, platform_account_id, type, value,
                normalized_value, source, validation_status, is_current,
                possible_duplicate_contact, first_seen_at, last_seen_at,
                source_updated_at, first_import_job_id, first_import_row_id,
                last_import_job_id, last_import_row_id
            ) VALUES (
                :id, :influencer_id, :account_id, 'email'::contact_type,
                'migration@example.invalid', 'migration@example.invalid',
                'huitun'::data_source, 'valid'::contact_validation_status,
                true, false, :observed_at, :observed_at, :observed_at,
                :job_id, :row_id, :job_id, :row_id
            )
            """
        ),
        {
            "id": contact_id,
            "influencer_id": influencer_id,
            "account_id": account_id,
            "observed_at": observed_at,
            "job_id": job_id,
            "row_id": row_id,
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
                :observed_at, CAST(:metrics AS jsonb), :hash,
                :job_id, :row_id
            )
            """
        ),
        {
            "id": metrics_id,
            "influencer_id": influencer_id,
            "account_id": account_id,
            "observed_at": observed_at,
            "metrics": json.dumps({"followers_count": 1234}),
            "hash": "e" * 64,
            "job_id": job_id,
            "row_id": row_id,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO influencer_metric_snapshots (
                id, influencer_id, platform_account_id, source,
                source_updated_at, import_job_id, import_row_id, captured_at,
                metrics, metrics_hash, snapshot_key
            ) VALUES (
                :id, :influencer_id, :account_id, 'huitun'::data_source,
                :observed_at, :job_id, :row_id, :observed_at,
                CAST(:metrics AS jsonb), :hash, :snapshot_key
            )
            """
        ),
        {
            "id": snapshot_id,
            "influencer_id": influencer_id,
            "account_id": account_id,
            "observed_at": observed_at,
            "job_id": job_id,
            "row_id": row_id,
            "metrics": json.dumps({"followers_count": 1234}),
            "hash": "e" * 64,
            "snapshot_key": "f" * 64,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO platform_account_source_identities (
                id, platform_account_id, platform, source, external_account_id,
                first_import_job_id, first_import_row_id,
                last_import_job_id, last_import_row_id
            ) VALUES (
                :id, :account_id, 'xiaohongshu'::platform_enum,
                'huitun'::data_source, 'migration-provider-id',
                :job_id, :row_id, :job_id, :row_id
            )
            """
        ),
        {
            "id": source_identity_id,
            "account_id": account_id,
            "job_id": job_id,
            "row_id": row_id,
        },
    )
    return {
        "influencer_id": influencer_id,
        "account_id": account_id,
        "source_state_id": source_state_id,
        "contact_id": contact_id,
        "metrics_id": metrics_id,
        "snapshot_id": snapshot_id,
        "source_identity_id": source_identity_id,
    }


def _assert_confirmed_lineage(connection: Connection, seeded: dict[str, Any]) -> None:
    confirmed = seeded["confirmed_ids"]
    assert confirmed is not None
    row_id = seeded["row_id"]
    job_id = seeded["job_ids"][0]
    checks = (
        ("influencers", "id", confirmed["influencer_id"]),
        ("influencer_platform_accounts", "id", confirmed["account_id"]),
        ("influencer_source_states", "last_import_row_id", row_id),
        ("influencer_contacts", "last_import_row_id", row_id),
        ("influencer_current_metrics", "last_import_row_id", row_id),
        ("influencer_metric_snapshots", "import_row_id", row_id),
        ("platform_account_source_identities", "last_import_row_id", row_id),
    )
    for table_name, column_name, expected in checks:
        assert (
            connection.scalar(
                text(f"SELECT count(*) FROM {table_name} WHERE {column_name} = :value"),
                {"value": expected},
            )
            == 1
        )
    row_lineage = connection.execute(
        text(
            """
            SELECT matched_influencer_id, matched_platform_account_id,
                   committed_action::text, committed_at
            FROM import_rows
            WHERE id = :row_id AND import_job_id = :job_id
            """
        ),
        {"row_id": row_id, "job_id": job_id},
    ).one()
    assert row_lineage.matched_influencer_id == confirmed["influencer_id"]
    assert row_lineage.matched_platform_account_id == confirmed["account_id"]
    assert row_lineage.committed_action == "create"
    assert row_lineage.committed_at is not None


def _seed_legacy_graph(
    connection: Connection,
    *,
    statuses: tuple[str, ...] = ("completed",),
    include_row: bool = True,
) -> dict[str, Any]:
    department_id = uuid4()
    operator_id = uuid4()
    collection_job_id = uuid4()
    stored_file_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO departments (
                id, name, password_hash, status, session_days
            ) VALUES (
                :id, :name, 'not-a-real-password-hash',
                'active'::department_status, 30
            )
            """
        ),
        {"id": department_id, "name": f"Phase 2 migration {uuid4().hex}"},
    )
    connection.execute(
        text(
            """
            INSERT INTO operators (id, department_id, name, role, status)
            VALUES (
                :id, :department_id, :name,
                'operator'::role_enum, 'active'::operator_status
            )
            """
        ),
        {
            "id": operator_id,
            "department_id": department_id,
            "name": f"Migration operator {uuid4().hex}",
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO collection_jobs (
                id, name, industry, purpose, target_action, target_count,
                department_id, owner_operator_id, source_type, status
            ) VALUES (
                :id, :name, '测试行业', '迁移回填验证', '导入', 100,
                :department_id, :operator_id,
                'manual_huitun_export'::import_source_type,
                'active'::collection_job_status
            )
            """
        ),
        {
            "id": collection_job_id,
            "name": f"Migration collection {uuid4().hex}",
            "department_id": department_id,
            "operator_id": operator_id,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO stored_import_files (
                id, sha256, storage_key, size, detected_type,
                detected_mime, encoding, expires_at
            ) VALUES (
                :id, :sha256, :storage_key, 1024,
                'csv'::stored_file_type, 'text/csv', 'utf-8', :expires_at
            )
            """
        ),
        {
            "id": stored_file_id,
            "sha256": "a" * 64,
            "storage_key": f"migration/{uuid4().hex}.csv",
            "expires_at": datetime.now(UTC) + timedelta(days=30),
        },
    )

    job_ids: list[UUID] = []
    row_id: UUID | None = None
    for index, status in enumerate(statuses):
        job_id = uuid4()
        job_ids.append(job_id)
        preview_revision = 1 if status in {"completed", "preview_ready", "preview_stale"} else 0
        connection.execute(
            text(
                """
                INSERT INTO import_jobs (
                    id, collection_job_id, department_id, operator_id,
                    stored_file_id, original_filename, mime_type, file_size,
                    sha256, source_type, status, detected_fields, field_mapping,
                    mapping_hash, preview_revision, total_rows, valid_rows,
                    warning_rows, error_rows, parse_task_id
                ) VALUES (
                    :id, :collection_job_id, :department_id, :operator_id,
                    :stored_file_id, :original_filename, 'text/csv', 1024,
                    :sha256, 'manual_huitun_export'::import_source_type,
                    CAST(:status AS import_job_status),
                    CAST(:detected_fields AS jsonb), CAST(:field_mapping AS jsonb),
                    :mapping_hash, :preview_revision, :total_rows, :valid_rows,
                    :warning_rows, :error_rows, :parse_task_id
                )
                """
            ),
            {
                "id": job_id,
                "collection_job_id": collection_job_id,
                "department_id": department_id,
                "operator_id": operator_id,
                "stored_file_id": stored_file_id,
                "original_filename": f"legacy-{index}-{status}.csv",
                "sha256": "a" * 64,
                "status": status,
                "detected_fields": json.dumps(["达人名称", "小红书号"]),
                "field_mapping": json.dumps({"达人名称": "account_name"}),
                "mapping_hash": "b" * 64,
                "preview_revision": preview_revision,
                "total_rows": 1 if index == 0 and include_row else 0,
                "valid_rows": 1 if index == 0 and include_row else 0,
                "warning_rows": 0,
                "error_rows": 0,
                "parse_task_id": f"legacy-parse-{index}",
            },
        )
        if index == 0 and include_row:
            row_id = uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO import_rows (
                        id, import_job_id, row_number, raw_data, normalized_data,
                        match_type, action, merge_plan, warnings, errors,
                        preview_revision, plan_hash
                    ) VALUES (
                        :id, :import_job_id, 2,
                        CAST(:raw_data AS jsonb), CAST(:normalized_data AS jsonb),
                        'none'::import_match_type, 'create'::import_row_action,
                        CAST(:merge_plan AS jsonb), '[]'::jsonb, '[]'::jsonb,
                        1, :plan_hash
                    )
                    """
                ),
                {
                    "id": row_id,
                    "import_job_id": job_id,
                    "raw_data": json.dumps({"达人名称": "迁移测试达人"}),
                    "normalized_data": json.dumps({"account_name": "迁移测试达人"}),
                    "merge_plan": json.dumps({"action": "create"}),
                    "plan_hash": "c" * 64,
                },
            )
    confirmed_ids = None
    if include_row and row_id is not None and statuses and statuses[0] == "completed":
        confirmed_ids = _seed_confirmed_lineage(
            connection,
            job_id=job_ids[0],
            row_id=row_id,
            operator_id=operator_id,
        )
    return {
        "department_id": department_id,
        "operator_id": operator_id,
        "collection_job_id": collection_job_id,
        "stored_file_id": stored_file_id,
        "job_ids": job_ids,
        "row_id": row_id,
        "confirmed_ids": confirmed_ids,
    }


def _insert_stored_file(connection: Connection) -> UUID:
    stored_file_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO stored_import_files (
                id, sha256, storage_key, size, detected_type,
                detected_mime, expires_at
            ) VALUES (
                :id, :sha256, :storage_key, 2048,
                'csv'::stored_file_type, 'text/csv', :expires_at
            )
            """
        ),
        {
            "id": stored_file_id,
            "sha256": uuid4().hex * 2,
            "storage_key": f"migration/{uuid4().hex}.csv",
            "expires_at": datetime.now(UTC) + timedelta(days=30),
        },
    )
    return stored_file_id


def _assert_database_rejects(
    connection: Connection,
    statement: str,
    parameters: dict[str, Any],
) -> None:
    with pytest.raises(DBAPIError):
        with connection.begin_nested():
            connection.execute(text(statement), parameters)


def test_fresh_upgrade_repeat_and_alembic_check(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("head")
    migration_database.upgrade("head")
    migration_database.check()

    with migration_database.engine.connect() as connection:
        _assert_postgresql_16(connection)
        assert _revision(connection) == "0007_phase3a_persistence_amendment"
        assert inspect(connection).has_table("import_job_files")
        assert inspect(connection).has_table("import_job_file_client_ids")
        assert inspect(connection).has_table("import_task_requests")
        import_job_file_columns = {
            column["name"]: column for column in inspect(connection).get_columns("import_job_files")
        }
        confirmation_required = import_job_file_columns["source_acquired_at_confirmation_required"]
        assert confirmation_required["nullable"] is False
        assert str(confirmation_required["default"]).lower() in {
            "false",
            "false::boolean",
        }
        assert "ck_import_job_file_acquisition_confirmation" in {
            constraint["name"]
            for constraint in inspect(connection).get_check_constraints("import_job_files")
        }
        assert _enum_values(connection, "import_task_kind") == [
            "legacy_parse",
            "file_parse",
            "preview",
            "confirm",
        ]
        assert _enum_values(connection, "import_task_state") == [
            "requested",
            "running",
            "retry_wait",
            "completed",
            "terminal_failed",
            "cancelled",
        ]


def test_0003_realistic_backfill_preserves_lineage_and_metadata(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("0003_phase1b")
    statuses = (
        "completed",
        "preview_ready",
        "preview_stale",
        "mapping_required",
        "failed",
        "cancelled",
    )
    with migration_database.engine.begin() as connection:
        seeded = _seed_legacy_graph(connection, statuses=statuses)

    migration_database.upgrade("0004_phase2_bulk_import")

    with migration_database.engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT job.id, file.position, file.status::text,
                       file.source_acquired_at, file.source_acquired_at_origin::text,
                       file.source_acquired_at_confirmation_required,
                       file.detected_fields, file.field_mapping, file.mapping_hash,
                       file.raw_rows, file.parse_task_id
                FROM import_jobs AS job
                JOIN import_job_files AS file ON file.import_job_id = job.id
                WHERE job.id = ANY(CAST(:job_ids AS uuid[]))
                ORDER BY array_position(CAST(:job_ids AS uuid[]), job.id)
                """
            ),
            {"job_ids": seeded["job_ids"]},
        ).mappings()
        occurrences = list(rows)
        assert [row["status"] for row in occurrences] == [
            "ready",
            "ready",
            "ready",
            "mapping_required",
            "failed",
            "excluded",
        ]
        for occurrence in occurrences:
            assert occurrence["position"] == 1
            assert occurrence["source_acquired_at"] is None
            assert occurrence["source_acquired_at_origin"] == "legacy_unknown"
            assert occurrence["source_acquired_at_confirmation_required"] is False
            assert occurrence["detected_fields"] == ["达人名称", "小红书号"]
            assert occurrence["field_mapping"] == {"达人名称": "account_name"}
            assert occurrence["mapping_hash"] == "b" * 64
            assert occurrence["parse_task_id"].startswith("legacy-parse-")

        assert (
            connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM import_job_file_client_ids
                    WHERE import_job_id = ANY(CAST(:job_ids AS uuid[]))
                    """
                ),
                {"job_ids": seeded["job_ids"]},
            )
            == 0
        )
        assert connection.scalar(text("SELECT count(*) FROM import_task_requests")) == 0

        row_lineage = connection.execute(
            text(
                """
                SELECT row.import_job_file_id, file.import_job_id
                FROM import_rows AS row
                JOIN import_job_files AS file
                  ON file.id = row.import_job_file_id
                 AND file.import_job_id = row.import_job_id
                WHERE row.id = :row_id
                """
            ),
            {"row_id": seeded["row_id"]},
        ).one()
        assert row_lineage.import_job_file_id is not None
        assert row_lineage.import_job_id == seeded["job_ids"][0]

        screening = connection.execute(
            text(
                """
                SELECT screening_rules, screening_rules_revision
                FROM collection_jobs WHERE id = :id
                """
            ),
            {"id": seeded["collection_job_id"]},
        ).one()
        assert screening.screening_rules == DEFAULT_SCREENING_RULES
        assert screening.screening_rules_revision == 1

        import_job_columns = {
            column["name"]: column for column in inspect(connection).get_columns("import_jobs")
        }
        import_job_file_columns = {
            column["name"] for column in inspect(connection).get_columns("import_job_files")
        }
        assert "client_file_id" not in import_job_file_columns
        for column_name in (
            "stored_file_id",
            "original_filename",
            "mime_type",
            "file_size",
            "sha256",
        ):
            assert import_job_columns[column_name]["nullable"] is True
        assert import_job_columns["failed_stage"]["nullable"] is True

        index_definition = connection.scalar(
            text(
                """
                SELECT indexdef FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND indexname = 'ix_import_rows_account_committed_job'
                """
            )
        )
        assert index_definition is not None
        assert "(matched_platform_account_id, committed_at DESC, import_job_id)" in str(
            index_definition
        )

        assert "draft" in _enum_values(connection, "import_job_status")
        assert _enum_values(connection, "import_job_file_status") == [
            "uploaded",
            "parsing",
            "mapping_required",
            "ready",
            "failed",
            "excluded",
        ]
        assert _enum_values(connection, "source_acquired_at_origin") == [
            "server_default",
            "user_confirmed",
            "legacy_unknown",
        ]
        assert _enum_values(connection, "import_job_failed_stage") == ["preview", "confirm"]
        phase2_audit_actions = {
            "IMPORT_BATCH_CREATED",
            "IMPORT_BATCH_PREVIEW_CREATED",
            "IMPORT_BATCH_CONFIRM_REQUESTED",
            "IMPORT_BATCH_COMPLETED",
            "IMPORT_BATCH_FAILED",
            "IMPORT_BATCH_RETRIED",
            "IMPORT_BATCH_CANCELLED",
            "IMPORT_FILE_REPLACED",
            "IMPORT_FILE_EXCLUDED",
            "IMPORT_FILE_RETRIED",
            "IMPORT_FILE_MAPPING_UPDATED",
            "IMPORT_FILE_SOURCE_ACQUIRED_AT_UPDATED",
            "COLLECTION_SCREENING_RULES_UPDATED",
        }
        assert phase2_audit_actions <= set(_enum_values(connection, "audit_action"))
        _assert_confirmed_lineage(connection, seeded)


def test_safe_legacy_downgrade_and_reupgrade_preserve_0003_data(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("0003_phase1b")
    with migration_database.engine.begin() as connection:
        seeded = _seed_legacy_graph(connection)

    migration_database.upgrade("0004_phase2_bulk_import")
    migration_database.downgrade("0003_phase1b")

    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0003_phase1b"
        assert not inspect(connection).has_table("import_job_files")
        assert not inspect(connection).has_table("import_job_file_client_ids")
        assert not inspect(connection).has_table("import_task_requests")
        assert "import_job_file_id" not in {
            column["name"] for column in inspect(connection).get_columns("import_rows")
        }
        assert (
            connection.scalar(
                text("SELECT count(*) FROM import_jobs WHERE id = :id"),
                {"id": seeded["job_ids"][0]},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM import_rows WHERE id = :id"),
                {"id": seeded["row_id"]},
            )
            == 1
        )
        _assert_confirmed_lineage(connection, seeded)

    migration_database.upgrade("0004_phase2_bulk_import")
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0004_phase2_bulk_import"
        assert (
            connection.scalar(
                text("SELECT count(*) FROM import_job_files WHERE import_job_id = :id"),
                {"id": seeded["job_ids"][0]},
            )
            == 1
        )
        assert connection.scalar(text("SELECT count(*) FROM import_job_file_client_ids")) == 0
        assert connection.scalar(text("SELECT count(*) FROM import_task_requests")) == 0
        assert (
            connection.scalar(
                text(
                    """
                    SELECT source_acquired_at_confirmation_required
                    FROM import_job_files
                    WHERE import_job_id = :job_id
                    """
                ),
                {"job_id": seeded["job_ids"][0]},
            )
            is False
        )
        _assert_confirmed_lineage(connection, seeded)


def test_acquisition_confirmation_states_and_check_constraint(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("0003_phase1b")
    with migration_database.engine.begin() as connection:
        seeded = _seed_legacy_graph(connection)
    migration_database.upgrade("0004_phase2_bulk_import")

    with migration_database.engine.begin() as connection:
        job_id = seeded["job_ids"][0]
        observed_at = datetime.now(UTC)
        insert_file_with_default = """
            INSERT INTO import_job_files (
                id, import_job_id, stored_file_id, position,
                original_filename, status, source_acquired_at,
                source_acquired_at_origin
            ) VALUES (
                :id, :job_id, :stored_file_id, :position,
                :original_filename, 'uploaded', :source_acquired_at,
                CAST(:origin AS source_acquired_at_origin)
            )
        """
        insert_file_with_confirmation = """
            INSERT INTO import_job_files (
                id, import_job_id, stored_file_id, position,
                original_filename, status, source_acquired_at,
                source_acquired_at_origin,
                source_acquired_at_confirmation_required
            ) VALUES (
                :id, :job_id, :stored_file_id, :position,
                :original_filename, 'uploaded', :source_acquired_at,
                CAST(:origin AS source_acquired_at_origin),
                :confirmation_required
            )
        """

        default_file_id = uuid4()
        connection.execute(
            text(insert_file_with_default),
            {
                "id": default_file_id,
                "job_id": job_id,
                "stored_file_id": _insert_stored_file(connection),
                "position": 2,
                "original_filename": "server-default.csv",
                "source_acquired_at": observed_at,
                "origin": "server_default",
            },
        )

        confirmation_file_id = uuid4()
        connection.execute(
            text(insert_file_with_confirmation),
            {
                "id": confirmation_file_id,
                "job_id": job_id,
                "stored_file_id": _insert_stored_file(connection),
                "position": 3,
                "original_filename": "historical-sha.csv",
                "source_acquired_at": observed_at,
                "origin": "server_default",
                "confirmation_required": True,
            },
        )

        user_confirmed_file_id = uuid4()
        connection.execute(
            text(insert_file_with_confirmation),
            {
                "id": user_confirmed_file_id,
                "job_id": job_id,
                "stored_file_id": _insert_stored_file(connection),
                "position": 4,
                "original_filename": "user-confirmed.csv",
                "source_acquired_at": observed_at,
                "origin": "user_confirmed",
                "confirmation_required": False,
            },
        )

        legacy_file_id = connection.scalar(
            text(
                """
                SELECT id FROM import_job_files
                WHERE import_job_id = :job_id AND position = 1
                """
            ),
            {"job_id": job_id},
        )
        states = connection.execute(
            text(
                """
                SELECT id, source_acquired_at, source_acquired_at_origin::text,
                       source_acquired_at_confirmation_required
                FROM import_job_files
                WHERE id = ANY(CAST(:file_ids AS uuid[]))
                """
            ),
            {
                "file_ids": [
                    legacy_file_id,
                    default_file_id,
                    confirmation_file_id,
                    user_confirmed_file_id,
                ]
            },
        ).mappings()
        states_by_id = {state["id"]: state for state in states}

        assert states_by_id[legacy_file_id] == {
            "id": legacy_file_id,
            "source_acquired_at": None,
            "source_acquired_at_origin": "legacy_unknown",
            "source_acquired_at_confirmation_required": False,
        }
        assert states_by_id[default_file_id]["source_acquired_at"] == observed_at
        assert states_by_id[default_file_id]["source_acquired_at_origin"] == "server_default"
        assert states_by_id[default_file_id]["source_acquired_at_confirmation_required"] is False
        assert states_by_id[confirmation_file_id]["source_acquired_at"] == observed_at
        assert states_by_id[confirmation_file_id]["source_acquired_at_origin"] == "server_default"
        assert (
            states_by_id[confirmation_file_id]["source_acquired_at_confirmation_required"] is True
        )
        assert states_by_id[user_confirmed_file_id]["source_acquired_at"] == observed_at
        assert states_by_id[user_confirmed_file_id]["source_acquired_at_origin"] == "user_confirmed"
        assert (
            states_by_id[user_confirmed_file_id]["source_acquired_at_confirmation_required"]
            is False
        )

        invalid_states = (
            (None, "server_default"),
            (observed_at, "user_confirmed"),
            (None, "legacy_unknown"),
        )
        for offset, (source_acquired_at, origin) in enumerate(invalid_states, start=5):
            _assert_database_rejects(
                connection,
                insert_file_with_confirmation,
                {
                    "id": uuid4(),
                    "job_id": job_id,
                    "stored_file_id": _insert_stored_file(connection),
                    "position": offset,
                    "original_filename": f"invalid-{offset}.csv",
                    "source_acquired_at": source_acquired_at,
                    "origin": origin,
                    "confirmation_required": True,
                },
            )


@pytest.mark.parametrize(
    "unsafe_change", ["multi_file", "nonlegacy_single", "alias", "screening", "durable_task"]
)
def test_downgrade_rejects_non_projectable_phase2_data(
    migration_database: MigrationDatabase,
    unsafe_change: str,
) -> None:
    migration_database.upgrade("0003_phase1b")
    with migration_database.engine.begin() as connection:
        seeded = _seed_legacy_graph(connection)
    migration_database.upgrade("0004_phase2_bulk_import")

    with migration_database.engine.begin() as connection:
        if unsafe_change == "multi_file":
            stored_file_id = _insert_stored_file(connection)
            connection.execute(
                text(
                    """
                    INSERT INTO import_job_files (
                        id, import_job_id, stored_file_id, position,
                        original_filename, declared_mime, status,
                        source_acquired_at, source_acquired_at_origin
                    ) VALUES (
                        :id, :job_id, :stored_file_id, 2,
                        'bulk-file-2.csv', 'text/csv', 'ready',
                        :source_acquired_at, 'server_default'
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "job_id": seeded["job_ids"][0],
                    "stored_file_id": stored_file_id,
                    "source_acquired_at": datetime.now(UTC),
                },
            )
        elif unsafe_change == "nonlegacy_single":
            connection.execute(
                text(
                    """
                    UPDATE import_job_files
                    SET source_acquired_at = :source_acquired_at,
                        source_acquired_at_origin = 'server_default'
                    WHERE import_job_id = :job_id
                    """
                ),
                {
                    "job_id": seeded["job_ids"][0],
                    "source_acquired_at": datetime.now(UTC),
                },
            )
        elif unsafe_change == "alias":
            file_id = connection.scalar(
                text("SELECT id FROM import_job_files WHERE import_job_id = :job_id"),
                {"job_id": seeded["job_ids"][0]},
            )
            assert file_id is not None
            connection.execute(
                text(
                    """
                    INSERT INTO import_job_file_client_ids (
                        id, import_job_id, import_job_file_id, client_file_id
                    ) VALUES (
                        :id, :job_id, :file_id, 'bulk-single'
                    )
                    """
                ),
                {"id": uuid4(), "job_id": seeded["job_ids"][0], "file_id": file_id},
            )
        elif unsafe_change == "screening":
            connection.execute(
                text(
                    """
                    UPDATE collection_jobs
                    SET screening_rules = :rules,
                        screening_rules_revision = 2
                    WHERE id = :collection_job_id
                    """
                ),
                {
                    "collection_job_id": seeded["collection_job_id"],
                    "rules": json.dumps(
                        {
                            "schema_version": 1,
                            "platforms": ["xiaohongshu"],
                            "source_tags_exact_any": ["美妆"],
                        }
                    ),
                },
            )
        else:
            connection.execute(
                text(
                    """
                    INSERT INTO import_task_requests (
                        id, task_token, task_kind, import_job_id, state
                    ) VALUES (
                        :id, :task_token, 'legacy_parse', :job_id, 'requested'
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "task_token": uuid4(),
                    "job_id": seeded["job_ids"][0],
                },
            )

    with pytest.raises(RuntimeError, match="downgrade blocked"):
        migration_database.downgrade("0003_phase1b")
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0004_phase2_bulk_import"


def test_upgrade_rejects_active_legacy_jobs(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("0003_phase1b")
    active_statuses = ("uploaded", "parsing", "previewing", "confirm_queued", "importing")
    with migration_database.engine.begin() as connection:
        _seed_legacy_graph(connection, statuses=active_statuses, include_row=False)

    with pytest.raises(RuntimeError, match="active legacy import jobs"):
        migration_database.upgrade("0004_phase2_bulk_import")
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0003_phase1b"
        assert not inspect(connection).has_table("import_job_files")


def test_postgresql_constraints_protect_file_and_row_lineage(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("0003_phase1b")
    with migration_database.engine.begin() as connection:
        seeded = _seed_legacy_graph(connection, statuses=("completed", "completed"))
    migration_database.upgrade("0004_phase2_bulk_import")

    with migration_database.engine.begin() as connection:
        job_one, job_two = seeded["job_ids"]
        file_one = connection.scalar(
            text("SELECT id FROM import_job_files WHERE import_job_id = :job_id"),
            {"job_id": job_one},
        )
        file_two = connection.scalar(
            text("SELECT id FROM import_job_files WHERE import_job_id = :job_id"),
            {"job_id": job_two},
        )
        second_stored_file = _insert_stored_file(connection)

        insert_file = """
            INSERT INTO import_job_files (
                id, import_job_id, stored_file_id, position,
                original_filename, status, source_acquired_at,
                source_acquired_at_origin, raw_rows, warning_rows,
                error_rows, parse_attempts
            ) VALUES (
                :id, :job_id, :stored_file_id, :position,
                'constraint.csv', CAST(:status AS import_job_file_status),
                :source_acquired_at,
                CAST(:origin AS source_acquired_at_origin),
                :raw_rows, 0, 0, 0
            )
        """
        base_file_parameters = {
            "id": uuid4(),
            "job_id": job_one,
            "stored_file_id": second_stored_file,
            "position": 2,
            "status": "ready",
            "source_acquired_at": datetime.now(UTC),
            "origin": "server_default",
            "raw_rows": 1,
        }

        _assert_database_rejects(
            connection,
            insert_file,
            {**base_file_parameters, "id": uuid4(), "position": 1},
        )
        _assert_database_rejects(
            connection,
            insert_file,
            {
                **base_file_parameters,
                "id": uuid4(),
                "stored_file_id": seeded["stored_file_id"],
            },
        )
        _assert_database_rejects(
            connection,
            insert_file,
            {
                **base_file_parameters,
                "id": uuid4(),
                "source_acquired_at": datetime.now(UTC),
                "origin": "legacy_unknown",
            },
        )
        _assert_database_rejects(
            connection,
            insert_file,
            {**base_file_parameters, "id": uuid4(), "position": 0},
        )
        _assert_database_rejects(
            connection,
            insert_file,
            {**base_file_parameters, "id": uuid4(), "raw_rows": -1},
        )
        _assert_database_rejects(
            connection,
            insert_file,
            {**base_file_parameters, "id": uuid4(), "status": "unknown"},
        )
        _assert_database_rejects(
            connection,
            """
            UPDATE collection_jobs SET screening_rules_revision = 0
            WHERE id = :collection_job_id
            """,
            {"collection_job_id": seeded["collection_job_id"]},
        )

        assert file_one is not None
        assert file_two is not None
        _assert_database_rejects(
            connection,
            """
            INSERT INTO import_rows (
                id, import_job_id, import_job_file_id, row_number, raw_data,
                match_type, action, warnings, errors, preview_revision, plan_hash
            ) VALUES (
                :id, :job_id, :file_id, 3, '{}'::jsonb,
                'none', 'create', '[]'::jsonb, '[]'::jsonb, 1, :plan_hash
            )
            """,
            {
                "id": uuid4(),
                "job_id": job_one,
                "file_id": file_two,
                "plan_hash": "d" * 64,
            },
        )

        second_file_id = uuid4()
        connection.execute(
            text(insert_file),
            {**base_file_parameters, "id": second_file_id},
        )
        insert_alias = """
            INSERT INTO import_job_file_client_ids (
                id, import_job_id, import_job_file_id, client_file_id
            ) VALUES (
                :id, :job_id, :file_id, :client_file_id
            )
        """
        connection.execute(
            text(insert_alias),
            {
                "id": uuid4(),
                "job_id": job_one,
                "file_id": file_one,
                "client_file_id": "shared-client-id",
            },
        )
        # The same client id is valid in a different ImportJob scope.
        connection.execute(
            text(insert_alias),
            {
                "id": uuid4(),
                "job_id": job_two,
                "file_id": file_two,
                "client_file_id": "shared-client-id",
            },
        )
        # One occurrence can retain every distinct client-side alias that resolved to it.
        connection.execute(
            text(insert_alias),
            {
                "id": uuid4(),
                "job_id": job_one,
                "file_id": file_one,
                "client_file_id": "second-alias-same-file",
            },
        )
        _assert_database_rejects(
            connection,
            insert_alias,
            {
                "id": uuid4(),
                "job_id": job_one,
                "file_id": file_one,
                "client_file_id": "shared-client-id",
            },
        )
        _assert_database_rejects(
            connection,
            insert_alias,
            {
                "id": uuid4(),
                "job_id": job_one,
                "file_id": second_file_id,
                "client_file_id": "shared-client-id",
            },
        )
        _assert_database_rejects(
            connection,
            insert_alias,
            {
                "id": uuid4(),
                "job_id": job_one,
                "file_id": file_two,
                "client_file_id": "mismatched-file-job",
            },
        )
        insert_row = """
            INSERT INTO import_rows (
                id, import_job_id, import_job_file_id, row_number, raw_data,
                match_type, action, warnings, errors, preview_revision, plan_hash
            ) VALUES (
                :id, :job_id, :file_id, 2, '{}'::jsonb,
                'none', 'create', '[]'::jsonb, '[]'::jsonb, 1, :plan_hash
            )
        """
        connection.execute(
            text(insert_row),
            {
                "id": uuid4(),
                "job_id": job_one,
                "file_id": second_file_id,
                "plan_hash": "e" * 64,
            },
        )
        _assert_database_rejects(
            connection,
            insert_row,
            {
                "id": uuid4(),
                "job_id": job_one,
                "file_id": second_file_id,
                "plan_hash": "f" * 64,
            },
        )

        # The same immutable blob is valid in a different historical ImportJob.
        assert (
            connection.scalar(
                text(
                    """
                SELECT count(*) FROM import_job_files
                WHERE stored_file_id = :stored_file_id
                  AND import_job_id = ANY(CAST(:job_ids AS uuid[]))
                """
                ),
                {"stored_file_id": seeded["stored_file_id"], "job_ids": [job_one, job_two]},
            )
            == 2
        )

        constraint_names = set(
            connection.scalars(
                text(
                    """
                    SELECT constraint_name
                    FROM information_schema.table_constraints
                    WHERE table_schema = current_schema()
                      AND table_name IN (
                          'import_job_files',
                          'import_job_file_client_ids',
                          'import_rows'
                      )
                    """
                )
            )
        )
        assert {
            "uq_import_job_file_position",
            "uq_import_job_file_stored_file",
            "uq_import_job_file_job_pair",
            "uq_import_job_file_client_id_alias",
            "fk_import_job_file_client_id_file_job",
            "uq_import_rows_file_number",
            "fk_import_row_file_job",
        } <= constraint_names

        screening_column = connection.execute(
            text(
                """
                SELECT data_type FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'collection_jobs'
                  AND column_name = 'screening_rules'
                """
            )
        ).scalar_one()
        assert screening_column == "jsonb"


def test_durable_import_task_constraints_and_reconciliation_indexes(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("0003_phase1b")
    with migration_database.engine.begin() as connection:
        seeded = _seed_legacy_graph(connection, statuses=("completed", "completed"))
    migration_database.upgrade("0004_phase2_bulk_import")

    insert_task = """
        INSERT INTO import_task_requests (
            id, task_token, task_kind, import_job_id, import_job_file_id,
            preview_revision, state, dispatch_attempts, run_attempts,
            next_retry_at, lease_expires_at, completed_at
        ) VALUES (
            :id, :task_token, CAST(:task_kind AS import_task_kind),
            :job_id, :file_id, :preview_revision,
            CAST(:state AS import_task_state), :dispatch_attempts, :run_attempts,
            :next_retry_at, :lease_expires_at, :completed_at
        )
    """

    def task_parameters(
        *,
        job_id: UUID,
        task_kind: str,
        state: str = "requested",
        file_id: UUID | None = None,
        preview_revision: int | None = None,
        task_token: UUID | None = None,
        dispatch_attempts: int = 0,
        run_attempts: int = 0,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        return {
            "id": uuid4(),
            "task_token": task_token or uuid4(),
            "task_kind": task_kind,
            "job_id": job_id,
            "file_id": file_id,
            "preview_revision": preview_revision,
            "state": state,
            "dispatch_attempts": dispatch_attempts,
            "run_attempts": run_attempts,
            "next_retry_at": now if state == "retry_wait" else None,
            "lease_expires_at": now + timedelta(minutes=5) if state == "running" else None,
            "completed_at": now if state == "completed" else None,
        }

    with migration_database.engine.begin() as connection:
        job_one, job_two = seeded["job_ids"]
        file_one = connection.scalar(
            text("SELECT id FROM import_job_files WHERE import_job_id = :job_id"),
            {"job_id": job_one},
        )
        file_two = connection.scalar(
            text("SELECT id FROM import_job_files WHERE import_job_id = :job_id"),
            {"job_id": job_two},
        )
        assert isinstance(file_one, UUID)
        assert isinstance(file_two, UUID)

        shared_token = uuid4()
        connection.execute(
            text(insert_task),
            task_parameters(
                job_id=job_one,
                task_kind="legacy_parse",
                state="cancelled",
                task_token=shared_token,
            ),
        )
        _assert_database_rejects(
            connection,
            insert_task,
            task_parameters(
                job_id=job_two,
                task_kind="legacy_parse",
                state="cancelled",
                task_token=shared_token,
            ),
        )

        # The composite FK makes a file target inseparable from its owning job.
        _assert_database_rejects(
            connection,
            insert_task,
            task_parameters(job_id=job_one, task_kind="file_parse", file_id=file_two),
        )

        invalid_targets = (
            task_parameters(job_id=job_one, task_kind="file_parse"),
            task_parameters(
                job_id=job_one,
                task_kind="file_parse",
                file_id=file_one,
                preview_revision=1,
            ),
            task_parameters(job_id=job_one, task_kind="confirm"),
            task_parameters(job_id=job_one, task_kind="confirm", preview_revision=0),
            task_parameters(
                job_id=job_one,
                task_kind="confirm",
                file_id=file_one,
                preview_revision=1,
            ),
            task_parameters(
                job_id=job_one,
                task_kind="preview",
                preview_revision=1,
            ),
            task_parameters(job_id=job_one, task_kind="legacy_parse", file_id=file_one),
        )
        for parameters in invalid_targets:
            _assert_database_rejects(connection, insert_task, parameters)

        invalid_running = task_parameters(job_id=job_one, task_kind="legacy_parse", state="running")
        invalid_running["lease_expires_at"] = None
        invalid_retry_wait = task_parameters(
            job_id=job_one, task_kind="legacy_parse", state="retry_wait"
        )
        invalid_retry_wait["next_retry_at"] = None
        invalid_completed = task_parameters(
            job_id=job_one, task_kind="legacy_parse", state="completed"
        )
        invalid_completed["completed_at"] = None
        invalid_attempts = task_parameters(
            job_id=job_one,
            task_kind="legacy_parse",
            state="cancelled",
            dispatch_attempts=-1,
        )
        invalid_runs = task_parameters(
            job_id=job_one,
            task_kind="legacy_parse",
            state="cancelled",
            run_attempts=-1,
        )
        for parameters in (
            invalid_running,
            invalid_retry_wait,
            invalid_completed,
            invalid_attempts,
            invalid_runs,
        ):
            _assert_database_rejects(connection, insert_task, parameters)

        for column, value in (("task_kind", "future_task"), ("state", "unknown")):
            invalid_enum = task_parameters(
                job_id=job_one,
                task_kind="legacy_parse",
                state="cancelled",
            )
            invalid_enum[column] = value
            _assert_database_rejects(connection, insert_task, invalid_enum)

        active_cases = (
            (
                task_parameters(job_id=job_one, task_kind="legacy_parse"),
                task_parameters(job_id=job_one, task_kind="legacy_parse", state="running"),
            ),
            (
                task_parameters(job_id=job_one, task_kind="file_parse", file_id=file_one),
                task_parameters(
                    job_id=job_one,
                    task_kind="file_parse",
                    file_id=file_one,
                    state="retry_wait",
                ),
            ),
            (
                task_parameters(job_id=job_one, task_kind="preview"),
                task_parameters(job_id=job_one, task_kind="preview", state="running"),
            ),
            (
                task_parameters(job_id=job_one, task_kind="confirm", preview_revision=1),
                task_parameters(
                    job_id=job_one,
                    task_kind="confirm",
                    preview_revision=1,
                    state="retry_wait",
                ),
            ),
        )
        for accepted, duplicate in active_cases:
            connection.execute(text(insert_task), accepted)
            _assert_database_rejects(connection, insert_task, duplicate)

        # Historical terminal rows remain available, and confirm uniqueness is revision-scoped.
        connection.execute(
            text(insert_task),
            task_parameters(
                job_id=job_one,
                task_kind="confirm",
                preview_revision=1,
                state="terminal_failed",
            ),
        )
        connection.execute(
            text(insert_task),
            task_parameters(job_id=job_one, task_kind="confirm", preview_revision=2),
        )

        index_definitions = {
            row["indexname"]: row["indexdef"]
            for row in connection.execute(
                text(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND tablename = 'import_task_requests'
                    """
                )
            ).mappings()
        }
        assert {
            "ix_import_task_requests_due",
            "ix_import_task_requests_expired_lease",
            "uq_import_task_request_active_confirm",
            "uq_import_task_request_active_file_parse",
            "uq_import_task_request_active_legacy_parse",
            "uq_import_task_request_active_preview",
        } <= set(index_definitions)
        assert (
            "COALESCE(next_retry_at, requested_at)"
            in index_definitions["ix_import_task_requests_due"]
        )
        assert (
            "WHERE (state = 'running'::import_task_state)"
            in index_definitions["ix_import_task_requests_expired_lease"]
        )
