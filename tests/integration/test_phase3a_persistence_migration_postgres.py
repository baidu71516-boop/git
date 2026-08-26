"""PostgreSQL gates for the additive Phase 3A persistence amendment."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
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

PHASE3_TABLES = {
    "candidate_pools",
    "targeting_policies",
    "candidate_pool_runs",
    "candidate_pool_members",
    "campaigns",
    "campaign_members",
    "outreach_targets",
    "outreach_tasks",
    "outreach_events",
    "message_templates",
    "message_template_versions",
    "phase3a_idempotency_records",
}

OPERATION_SCOPES = [
    "CANDIDATE_POOL_CREATE",
    "TARGETING_POLICY_CREATE",
    "CAMPAIGN_CREATE",
    "CAMPAIGN_MEMBER_BULK_ADD",
    "OUTREACH_TARGET_CREATE",
]

PHASE3A_AUDIT_ACTIONS = {
    "CANDIDATE_POOL_CREATED",
    "CANDIDATE_POOL_UPDATED",
    "TARGETING_POLICY_CREATED",
    "CANDIDATE_POOL_RUN_REQUESTED",
    "CANDIDATE_POOL_RUN_COMPLETED",
    "CANDIDATE_POOL_RUN_FAILED",
    "CAMPAIGN_CREATED",
    "CAMPAIGN_UPDATED",
    "CAMPAIGN_MEMBERS_ADDED",
    "CAMPAIGN_MEMBERS_REMOVED",
    "OUTREACH_TARGET_CREATED",
    "OUTREACH_TARGET_UPDATED",
    "OUTREACH_TASK_CREATED",
    "OUTREACH_TASK_TRANSITIONED",
}


def _test_database_url() -> URL:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    try:
        url = make_url(raw_url)
    except ArgumentError as error:
        pytest.fail(f"TEST_DATABASE_URL is invalid: {error}", pytrace=False)
    database_name = (url.database or "").lower()
    if url.get_backend_name() != "postgresql" or "phase1b_test" not in database_name:
        pytest.fail("TEST_DATABASE_URL must be an explicitly named PostgreSQL test database")
    return url.set(drivername="postgresql+psycopg")


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
    database_url = _test_database_url()
    schema_name = f"phase3a_persistence_{uuid4().hex}"
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(CreateSchema(schema_name))
    scoped_url = database_url.update_query_dict({"options": f"-csearch_path={schema_name}"})
    monkeypatch.setenv(
        "DATABASE_URL", scoped_url.render_as_string(hide_password=False).replace("%", "%%")
    )
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


def _insert_department(connection: Connection) -> UUID:
    department_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO departments (id, name, password_hash, status, session_days)
            VALUES (:id, :name, 'not-a-hash', 'active'::department_status, 30)
            """
        ),
        {"id": department_id, "name": f"Phase 3A test {department_id.hex}"},
    )
    return department_id


def _seed_0006_candidate_pool(connection: Connection) -> Mapping[str, UUID]:
    department_id = _insert_department(connection)
    operator_id = uuid4()
    pool_id = uuid4()
    connection.execute(
        text(
            """
            INSERT INTO operators (id, department_id, name, role, status)
            VALUES (:id, :department_id, 'Phase 3A operator',
                    'operator'::role_enum, 'active'::operator_status)
            """
        ),
        {"id": operator_id, "department_id": department_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO candidate_pools (id, department_id, owner_operator_id, name, kind)
            VALUES (:id, :department_id, :operator_id, 'Existing 0006 pool',
                    'POTENTIAL_SELLER'::candidate_pool_kind)
            """
        ),
        {"id": pool_id, "department_id": department_id, "operator_id": operator_id},
    )
    return {"department_id": department_id, "operator_id": operator_id, "pool_id": pool_id}


def _insert_idempotency_record(
    connection: Connection,
    department_id: UUID,
    *,
    operation_scope: str = "CAMPAIGN_CREATE",
    idempotency_key: str = "idempotency-key",
    request_hash: str = "a" * 64,
    result_entity_id: UUID | None = None,
    result_schema_version: int = 1,
    payload: str | None = None,
) -> UUID:
    record_id = uuid4()
    result_entity_id = result_entity_id or uuid4()
    connection.execute(
        text(
            """
            INSERT INTO phase3a_idempotency_records (
                id, department_id, operation_scope, idempotency_key, request_hash,
                result_entity_id, result_schema_version, result_payload
            ) VALUES (
                :id, :department_id, CAST(:operation_scope AS phase3a_operation_scope),
                :idempotency_key, :request_hash, :result_entity_id, :result_schema_version,
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "id": record_id,
            "department_id": department_id,
            "operation_scope": operation_scope,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "result_entity_id": result_entity_id,
            "result_schema_version": result_schema_version,
            "payload": payload or json.dumps({"entity_id": str(result_entity_id)}),
        },
    )
    return record_id


def _assert_database_rejects(
    connection: Connection, statement: str, parameters: Mapping[str, object]
) -> None:
    with pytest.raises(DBAPIError):
        with connection.begin_nested():
            connection.execute(text(statement), parameters)


def test_fresh_upgrade_physical_contract_and_alembic_check(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("head")
    migration_database.upgrade("head")
    migration_database.check()

    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0008_content_activity_p0"
        assert int(connection.scalar(text("SHOW server_version_num"))) // 10_000 == 16
        assert (
            connection.scalar(
                text(
                    "SELECT character_maximum_length "
                    "FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'alembic_version' AND column_name = 'version_num'"
                )
            )
            == 64
        )
        inspector = inspect(connection)
        assert PHASE3_TABLES <= set(inspector.get_table_names())
        assert "uq_influencer_contact_id_influencer" in {
            item["name"] for item in inspector.get_unique_constraints("influencer_contacts")
        }
        idempotency_columns = {
            column["name"] for column in inspector.get_columns("phase3a_idempotency_records")
        }
        assert idempotency_columns == {
            "id",
            "department_id",
            "operation_scope",
            "idempotency_key",
            "request_hash",
            "result_entity_id",
            "result_schema_version",
            "result_payload",
            "created_at",
        }
        assert "uq_phase3a_idempotency_record_department_scope_key" in {
            item["name"] for item in inspector.get_unique_constraints("phase3a_idempotency_records")
        }
        assert "fk_phase3a_idempotency_record_department" in {
            item["name"] for item in inspector.get_foreign_keys("phase3a_idempotency_records")
        }
        assert {
            "ck_phase3a_idempotency_record_key_length",
            "ck_phase3a_idempotency_record_request_hash",
            "ck_phase3a_idempotency_record_result_schema_version",
            "ck_phase3a_idempotency_record_payload_object",
            "ck_phase3a_idempotency_record_payload_size",
            "ck_phase3a_idempotency_record_bulk_add_payload",
        } <= {
            item["name"] for item in inspector.get_check_constraints("phase3a_idempotency_records")
        }
        assert _enum_values(connection, "phase3a_operation_scope") == OPERATION_SCOPES
        assert PHASE3A_AUDIT_ACTIONS <= set(_enum_values(connection, "audit_action"))

        campaign_member_fks = {
            item["name"] for item in inspector.get_foreign_keys("campaign_members")
        }
        target_fks = {item["name"] for item in inspector.get_foreign_keys("outreach_targets")}
        target_checks = {
            item["name"] for item in inspector.get_check_constraints("outreach_targets")
        }
        assert "fk_campaign_member_preferred_account_influencer" in campaign_member_fks
        assert {
            "fk_outreach_target_contact_influencer",
            "fk_outreach_target_account_influencer",
        } <= target_fks
        assert "ck_outreach_target_channel_reference_shape" in target_checks
        assert connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'trg_outreach_events_append_only' AND NOT tgisinternal)"
            )
        )
        assert connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'trg_phase3a_idempotency_records_append_only' "
                "AND NOT tgisinternal)"
            )
        )


def test_0006_to_0007_preserves_existing_wo1_data(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("0006_phase3a_persistence")
    with migration_database.engine.begin() as connection:
        seeded = _seed_0006_candidate_pool(connection)

    migration_database.upgrade("0007_phase3a_persistence_amendment")
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0007_phase3a_persistence_amendment"
        assert (
            connection.scalar(
                text("SELECT name FROM candidate_pools WHERE id = :id"),
                {"id": seeded["pool_id"]},
            )
            == "Existing 0006 pool"
        )
        assert connection.scalar(text("SELECT count(*) FROM phase3a_idempotency_records")) == 0


def test_0005_to_0006_to_0007_preserves_prior_department_data(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("0005_phase2_refresh_queue")
    with migration_database.engine.begin() as connection:
        department_id = _insert_department(connection)

    migration_database.upgrade("0006_phase3a_persistence")
    migration_database.upgrade("0007_phase3a_persistence_amendment")
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0007_phase3a_persistence_amendment"
        assert (
            connection.scalar(
                text("SELECT count(*) FROM departments WHERE id = :id"),
                {"id": department_id},
            )
            == 1
        )


def test_idempotency_constraints_scoping_size_and_append_only_protection(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("head")
    with migration_database.engine.begin() as connection:
        department_id = _insert_department(connection)
        other_department_id = _insert_department(connection)
        record_id = _insert_idempotency_record(connection, department_id)

        _assert_database_rejects(
            connection,
            """
            INSERT INTO phase3a_idempotency_records (
                id, department_id, operation_scope, idempotency_key, request_hash,
                result_entity_id, result_schema_version, result_payload
            ) VALUES (
                :id, :department_id, 'CAMPAIGN_CREATE'::phase3a_operation_scope,
                'idempotency-key', :request_hash, :result_entity_id, 1, '{}'::jsonb
            )
            """,
            {
                "id": uuid4(),
                "department_id": department_id,
                "request_hash": "b" * 64,
                "result_entity_id": uuid4(),
            },
        )
        campaign_id = uuid4()
        _insert_idempotency_record(
            connection,
            department_id,
            operation_scope="CAMPAIGN_MEMBER_BULK_ADD",
            request_hash="b" * 64,
            result_entity_id=campaign_id,
            payload=json.dumps(
                {
                    "campaign_id": str(campaign_id),
                    "source_pool_run_id": None,
                    "requested_count": 0,
                    "added_count": 0,
                    "restored_count": 0,
                    "already_active_count": 0,
                    "active_count_after": 0,
                }
            ),
        )
        _insert_idempotency_record(
            connection,
            department_id,
            operation_scope="CAMPAIGN_MEMBER_BULK_ADD",
            idempotency_key="bulk-schema-v2",
            request_hash="d" * 64,
            result_schema_version=2,
            payload=json.dumps({"versioned_result": True}),
        )
        _insert_idempotency_record(
            connection,
            other_department_id,
            request_hash="b" * 64,
        )

        for key, request_hash, payload in (
            ("invalid-hash", "A" * 64, "{}"),
            ("array-payload", "c" * 64, "[]"),
            ("", "d" * 64, "{}"),
        ):
            _assert_database_rejects(
                connection,
                """
                INSERT INTO phase3a_idempotency_records (
                    id, department_id, operation_scope, idempotency_key, request_hash,
                    result_entity_id, result_schema_version, result_payload
                ) VALUES (
                    :id, :department_id, 'CAMPAIGN_CREATE'::phase3a_operation_scope,
                    :idempotency_key, :request_hash, :result_entity_id, 1,
                    CAST(:payload AS jsonb)
                )
                """,
                {
                    "id": uuid4(),
                    "department_id": department_id,
                    "idempotency_key": key,
                    "request_hash": request_hash,
                    "result_entity_id": uuid4(),
                    "payload": payload,
                },
            )

        payload_at_limit = json.dumps({"value": "x" * 16_371})
        assert (
            connection.scalar(
                text("SELECT octet_length(CAST(:payload AS jsonb)::text)"),
                {"payload": payload_at_limit},
            )
            == 16_384
        )
        _insert_idempotency_record(
            connection,
            department_id,
            idempotency_key="payload-at-limit",
            request_hash="e" * 64,
            payload=payload_at_limit,
        )
        _assert_database_rejects(
            connection,
            """
            INSERT INTO phase3a_idempotency_records (
                id, department_id, operation_scope, idempotency_key, request_hash,
                result_entity_id, result_schema_version, result_payload
            ) VALUES (
                :id, :department_id, 'CAMPAIGN_CREATE'::phase3a_operation_scope,
                'payload-over-limit', :request_hash, :result_entity_id, 1,
                CAST(:payload AS jsonb)
            )
            """,
            {
                "id": uuid4(),
                "department_id": department_id,
                "request_hash": "f" * 64,
                "result_entity_id": uuid4(),
                "payload": json.dumps({"value": "x" * 16_372}),
            },
        )
        _assert_database_rejects(
            connection,
            """
            INSERT INTO phase3a_idempotency_records (
                id, department_id, operation_scope, idempotency_key, request_hash,
                result_entity_id, result_schema_version, result_payload
            ) VALUES (
                :id, :department_id, 'CAMPAIGN_MEMBER_BULK_ADD'::phase3a_operation_scope,
                'bulk-member-list', :request_hash, :result_entity_id, 1,
                '{
                    "campaign_id": "00000000-0000-0000-0000-000000000000",
                    "source_pool_run_id": null,
                    "requested_count": 0,
                    "added_count": 0,
                    "restored_count": 0,
                    "already_active_count": 0,
                    "active_count_after": 0,
                    "member_ids": []
                }'::jsonb
            )
            """,
            {
                "id": uuid4(),
                "department_id": department_id,
                "request_hash": "0" * 64,
                "result_entity_id": UUID("00000000-0000-0000-0000-000000000000"),
            },
        )
        for idempotency_key, payload in (
            (
                "bulk-invalid-campaign-id",
                {
                    "campaign_id": "not-a-uuid",
                    "source_pool_run_id": None,
                    "requested_count": 0,
                    "added_count": 0,
                    "restored_count": 0,
                    "already_active_count": 0,
                    "active_count_after": 0,
                },
            ),
            (
                "bulk-negative-count",
                {
                    "campaign_id": str(campaign_id),
                    "source_pool_run_id": None,
                    "requested_count": 0,
                    "added_count": -1,
                    "restored_count": 0,
                    "already_active_count": 1,
                    "active_count_after": 0,
                },
            ),
            (
                "bulk-fractional-count",
                {
                    "campaign_id": str(campaign_id),
                    "source_pool_run_id": None,
                    "requested_count": 1.5,
                    "added_count": 0,
                    "restored_count": 0,
                    "already_active_count": 0,
                    "active_count_after": 0,
                },
            ),
            (
                "bulk-count-mismatch",
                {
                    "campaign_id": str(campaign_id),
                    "source_pool_run_id": None,
                    "requested_count": 2,
                    "added_count": 1,
                    "restored_count": 0,
                    "already_active_count": 0,
                    "active_count_after": 1,
                },
            ),
            (
                "bulk-invalid-source-run-id",
                {
                    "campaign_id": str(campaign_id),
                    "source_pool_run_id": "not-a-uuid",
                    "requested_count": 0,
                    "added_count": 0,
                    "restored_count": 0,
                    "already_active_count": 0,
                    "active_count_after": 0,
                },
            ),
        ):
            _assert_database_rejects(
                connection,
                """
                INSERT INTO phase3a_idempotency_records (
                    id, department_id, operation_scope, idempotency_key, request_hash,
                    result_entity_id, result_schema_version, result_payload
                ) VALUES (
                    :id, :department_id, 'CAMPAIGN_MEMBER_BULK_ADD'::phase3a_operation_scope,
                    :idempotency_key, :request_hash, :result_entity_id, 1,
                    CAST(:payload AS jsonb)
                )
                """,
                {
                    "id": uuid4(),
                    "department_id": department_id,
                    "idempotency_key": idempotency_key,
                    "request_hash": "1" * 64,
                    "result_entity_id": campaign_id,
                    "payload": json.dumps(payload),
                },
            )
        _assert_database_rejects(
            connection,
            """
            INSERT INTO phase3a_idempotency_records (
                id, department_id, operation_scope, idempotency_key, request_hash,
                result_entity_id, result_schema_version, result_payload
            ) VALUES (
                :id, :department_id, 'CAMPAIGN_MEMBER_BULK_ADD'::phase3a_operation_scope,
                'bulk-entity-mismatch', :request_hash, :result_entity_id, 1,
                CAST(:payload AS jsonb)
            )
            """,
            {
                "id": uuid4(),
                "department_id": department_id,
                "request_hash": "2" * 64,
                "result_entity_id": uuid4(),
                "payload": json.dumps(
                    {
                        "campaign_id": str(campaign_id),
                        "source_pool_run_id": None,
                        "requested_count": 0,
                        "added_count": 0,
                        "restored_count": 0,
                        "already_active_count": 0,
                        "active_count_after": 0,
                    }
                ),
            },
        )
        _assert_database_rejects(
            connection,
            "UPDATE phase3a_idempotency_records SET result_schema_version = 2 WHERE id = :id",
            {"id": record_id},
        )
        _assert_database_rejects(
            connection,
            "DELETE FROM phase3a_idempotency_records WHERE id = :id",
            {"id": record_id},
        )


def test_empty_downgrade_is_reversible_and_keeps_audit_enum_labels(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("head")
    migration_database.downgrade("0006_phase3a_persistence")
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0006_phase3a_persistence"
        assert not inspect(connection).has_table("phase3a_idempotency_records")
        assert (
            connection.scalar(
                text(
                    "SELECT character_maximum_length "
                    "FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'alembic_version' AND column_name = 'version_num'"
                )
            )
            == 64
        )
        assert connection.scalar(text("SELECT to_regtype('phase3a_operation_scope')")) is None
        assert PHASE3A_AUDIT_ACTIONS <= set(_enum_values(connection, "audit_action"))

    migration_database.upgrade("0007_phase3a_persistence_amendment")
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0007_phase3a_persistence_amendment"
    migration_database.upgrade("head")
    migration_database.check()


def test_downgrade_refuses_populated_idempotency_records(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("head")
    with migration_database.engine.begin() as connection:
        department_id = _insert_department(connection)
        record_id = _insert_idempotency_record(connection, department_id)

    with pytest.raises(RuntimeError, match="0007 downgrade blocked"):
        migration_database.downgrade("0006_phase3a_persistence")

    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0008_content_activity_p0"
        assert (
            connection.scalar(
                text("SELECT count(*) FROM phase3a_idempotency_records WHERE id = :id"),
                {"id": record_id},
            )
            == 1
        )
