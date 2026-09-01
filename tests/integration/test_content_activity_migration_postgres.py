"""PostgreSQL physical-contract tests for Content Activity P0 persistence.

Every test creates and drops a random schema in an explicitly named disposable
PostgreSQL test database. No production endpoint, credential, or provider call
is used.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from backend_core.config import Settings, get_settings
from backend_core.content_activity.service import ContentActivityService
from sqlalchemy import Connection, Engine, create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "infrastructure" / "migrations" / "alembic.ini"
MIGRATIONS = PROJECT_ROOT / "infrastructure" / "migrations"

CONTENT_ACTIVITY_TABLES = {
    "provider_account_identities",
    "provider_account_identity_verifications",
    "content_activity_observations",
    "content_activity_projections",
    "content_activity_refresh_requests",
}
XHS_IDENTITY_SOURCE = "TIKHUB_XHS_APP_V2"
XHS_IDENTITY_RESOLVER_CONTRACT_VERSION = "TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1"
XHS_ACTIVITY_PRODUCT = "XIAOHONGSHU_APP_V2"
XHS_ACTIVITY_ENDPOINT = "/api/v1/xiaohongshu/app_v2/get_user_posted_notes"
XHS_ACTIVITY_ENDPOINT_VERSION = "APP_V2"
XHS_ACTIVITY_ADAPTER_VERSION = "TIKHUB_XHS_CONTENT_ACTIVITY_ADAPTER_V1"
XHS_ACTIVITY_CAPABILITY_POLICY_VERSION = "TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1"
XHS_ACTIVITY_RESPONSE_SCHEMA_VERSION = "TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1"
XHS_ACTIVITY_VISIBILITY_POLICY_VERSION = "TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1"
HUITUN_IDENTITY_SOURCE = "HUITUN_DOUYIN_AWEME_LIST_RUNTIME_CAPTURE"
HUITUN_IDENTITY_CONTRACT_VERSION = "HUITUN_DOUYIN_AWEME_LIST_RUNTIME_SEMANTIC_V1"
HUITUN_ACTIVITY_PRODUCT = "HUITUN_DOUYIN_AWEME_LIST"
HUITUN_ACTIVITY_ENDPOINT = "/user/awemeList"
HUITUN_ACTIVITY_ENDPOINT_VERSION = "RUNTIME_SEMANTIC_V1"
HUITUN_ACTIVITY_ADAPTER_VERSION = "HUITUN_DOUYIN_RUNTIME_CAPTURE_ADAPTER_V1"
HUITUN_ACTIVITY_CAPABILITY_POLICY_VERSION = "HUITUN_DOUYIN_RUNTIME_CAPTURE_POLICY_V1"
HUITUN_ACTIVITY_RESPONSE_SCHEMA_VERSION = "HUITUN_DOUYIN_AWEME_LIST_SEMANTIC_SCHEMA_V1"
HUITUN_ACTIVITY_VISIBILITY_POLICY_VERSION = "HUITUN_DOUYIN_RETURNED_SCOPE_POLICY_V1"


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
    schema_name = f"content_activity_{uuid4().hex}"
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


def _assert_rejected(
    connection: Connection,
    statement: str,
    parameters: Mapping[str, object],
) -> None:
    with pytest.raises(DBAPIError):
        with connection.begin_nested():
            connection.execute(text(statement), parameters)


def _seed_account(connection: Connection, *, label: str) -> tuple[UUID, UUID]:
    influencer_id = uuid4()
    account_id = uuid4()
    connection.execute(
        text("INSERT INTO influencers (id, display_name) VALUES (:id, :display_name)"),
        {"id": influencer_id, "display_name": f"Content Activity {label}"},
    )
    connection.execute(
        text(
            """
            INSERT INTO influencer_platform_accounts (
                id, influencer_id, platform, platform_account_id, account_name, source
            ) VALUES (
                :id, :influencer_id, 'xiaohongshu'::platform_enum,
                :platform_account_id, :account_name, 'manual'::data_source
            )
            """
        ),
        {
            "id": account_id,
            "influencer_id": influencer_id,
            "platform_account_id": f"account-{label}",
            "account_name": f"Content Activity {label}",
        },
    )
    return influencer_id, account_id


def _seed_douyin_account(connection: Connection, *, label: str) -> tuple[UUID, UUID]:
    influencer_id = uuid4()
    account_id = uuid4()
    connection.execute(
        text("INSERT INTO influencers (id, display_name) VALUES (:id, :display_name)"),
        {"id": influencer_id, "display_name": f"Douyin Content Activity {label}"},
    )
    connection.execute(
        text(
            """
            INSERT INTO influencer_platform_accounts (
                id, influencer_id, platform, platform_account_id, account_name, source
            ) VALUES (
                :id, :influencer_id, 'douyin'::platform_enum,
                :platform_account_id, :account_name, 'manual'::data_source
            )
            """
        ),
        {
            "id": account_id,
            "influencer_id": influencer_id,
            "platform_account_id": f"douyin-account-{label}",
            "account_name": f"Douyin Content Activity {label}",
        },
    )
    return influencer_id, account_id


def _insert_identity(
    connection: Connection,
    *,
    account_id: UUID,
    external_identity: str,
    identity_source: str = XHS_IDENTITY_SOURCE,
    resolver_contract_version: str = XHS_IDENTITY_RESOLVER_CONTRACT_VERSION,
) -> tuple[UUID, UUID]:
    identity_id = uuid4()
    verification_id = uuid4()
    now = datetime(2026, 8, 23, tzinfo=UTC)
    connection.execute(
        text(
            """
            INSERT INTO provider_account_identities (
                id, platform_account_id, platform, namespace, opaque_external_identity,
                identity_source, resolver_contract_version, verification_state,
                resolved_at, verified_at, provenance_ref, lock_version
            ) VALUES (
                :id, :platform_account_id, 'xiaohongshu'::platform_enum,
                'xiaohongshu.userid'::provider_account_identity_namespace,
                :opaque_external_identity, :identity_source, :resolver_contract_version,
                'VERIFIED_CURRENT'::provider_account_identity_verification_state,
                :resolved_at, :verified_at, 'fixture-provenance', 1
            )
            """
        ),
        {
            "id": identity_id,
            "platform_account_id": account_id,
            "opaque_external_identity": external_identity,
            "identity_source": identity_source,
            "resolver_contract_version": resolver_contract_version,
            "resolved_at": now,
            "verified_at": now,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO provider_account_identity_verifications (
                id, provider_account_identity_id, identity_source, resolver_contract_version,
                verification_outcome, verified_at, provenance_ref, idempotency_key
            ) VALUES (
                :id, :provider_account_identity_id, :identity_source, :resolver_contract_version,
                'POLICY_VERIFIED'::provider_account_identity_verification_outcome,
                :verified_at, 'fixture-verification', :idempotency_key
            )
            """
        ),
        {
            "id": verification_id,
            "provider_account_identity_id": identity_id,
            "identity_source": identity_source,
            "resolver_contract_version": resolver_contract_version,
            "verified_at": now,
            "idempotency_key": f"fixture-{identity_id}",
        },
    )
    return identity_id, verification_id


def _insert_huitun_douyin_identity(
    connection: Connection,
    *,
    account_id: UUID,
    external_uid: str,
) -> tuple[UUID, UUID]:
    identity_id = uuid4()
    verification_id = uuid4()
    now = datetime(2026, 8, 23, tzinfo=UTC)
    connection.execute(
        text(
            """
            INSERT INTO provider_account_identities (
                id, platform_account_id, platform, namespace, opaque_external_identity,
                identity_source, resolver_contract_version, verification_state,
                resolved_at, verified_at, provenance_ref, lock_version
            ) VALUES (
                :id, :platform_account_id, 'douyin'::platform_enum,
                'douyin.huitun_uid'::provider_account_identity_namespace,
                :opaque_external_identity, :identity_source, :resolver_contract_version,
                'VERIFIED_CURRENT'::provider_account_identity_verification_state,
                :resolved_at, :verified_at, 'huitun-runtime-fixture', 1
            )
            """
        ),
        {
            "id": identity_id,
            "platform_account_id": account_id,
            "opaque_external_identity": external_uid,
            "identity_source": HUITUN_IDENTITY_SOURCE,
            "resolver_contract_version": HUITUN_IDENTITY_CONTRACT_VERSION,
            "resolved_at": now,
            "verified_at": now,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO provider_account_identity_verifications (
                id, provider_account_identity_id, identity_source, resolver_contract_version,
                verification_outcome, verified_at, provenance_ref, idempotency_key
            ) VALUES (
                :id, :provider_account_identity_id, :identity_source, :resolver_contract_version,
                'POLICY_VERIFIED'::provider_account_identity_verification_outcome,
                :verified_at, 'huitun-runtime-fixture', :idempotency_key
            )
            """
        ),
        {
            "id": verification_id,
            "provider_account_identity_id": identity_id,
            "identity_source": HUITUN_IDENTITY_SOURCE,
            "resolver_contract_version": HUITUN_IDENTITY_CONTRACT_VERSION,
            "verified_at": now,
            "idempotency_key": f"huitun-fixture-{identity_id}",
        },
    )
    return identity_id, verification_id


def _insert_complete_huitun_observation(
    connection: Connection,
    *,
    account_id: UUID,
    identity_id: UUID,
    verification_id: UUID,
    coverage_status: str = "LATEST_BOUND_PROVEN",
) -> UUID:
    observation_id = uuid4()
    observed_at = datetime(2026, 8, 23, tzinfo=UTC)
    connection.execute(
        text(
            """
            INSERT INTO content_activity_observations (
                id, platform_account_id, platform, schema_version, activity_semantics,
                provider_account_identity_id, provider_account_identity_verification_id,
                activity_source_provider, provider_product, endpoint, endpoint_version,
                adapter_version, capability_policy_version, response_schema_version,
                visibility_policy_version, attempt_started_at, observed_at,
                observation_status, coverage_status, activity_result, last_publication_at,
                latest_publication_id_namespace, latest_publication_id, latest_publication_type,
                co_latest_publication_count, coverage_start_at, coverage_end_at,
                timestamp_encoding, source_timezone, timezone_basis, normalized_timezone,
                request_ref, provenance_ref, scan_terminal_reason, scanned_page_count,
                scanned_item_count
            ) VALUES (
                :id, :platform_account_id, 'douyin'::platform_enum, 1,
                'HUITUN_RETURNED_SCOPE'::content_activity_semantics,
                :identity_id, :verification_id,
                'HUITUN_DOUYIN_AWEME_LIST'::content_activity_provider, :provider_product,
                :endpoint, :endpoint_version, :adapter_version, :capability_policy_version,
                :response_schema_version, :visibility_policy_version,
                :attempt_started_at, :observed_at,
                'COMPLETE'::content_activity_observation_status,
                CAST(:coverage_status AS content_activity_coverage_status),
                'PUBLICATION_FOUND'::content_activity_result, :last_publication_at,
                'huitun.douyin.aweme_list.timestamp.v1', :latest_publication_id,
                'OTHER'::content_activity_publication_type, 1, NULL, :coverage_end_at,
                'HUITUN_PUBLISH_TIME_ASIA_SHANGHAI', 'Asia/Shanghai',
                'HUITUN_PUBLISH_TIME_ASIA_SHANGHAI', 'UTC',
                :request_ref, 'huitun-runtime-fixture',
                'SINGLE_RESPONSE_COMPLETE'::content_activity_scan_terminal_reason, 1, 1
            )
            """
        ),
        {
            "id": observation_id,
            "platform_account_id": account_id,
            "identity_id": identity_id,
            "verification_id": verification_id,
            "provider_product": HUITUN_ACTIVITY_PRODUCT,
            "endpoint": HUITUN_ACTIVITY_ENDPOINT,
            "endpoint_version": HUITUN_ACTIVITY_ENDPOINT_VERSION,
            "adapter_version": HUITUN_ACTIVITY_ADAPTER_VERSION,
            "capability_policy_version": HUITUN_ACTIVITY_CAPABILITY_POLICY_VERSION,
            "response_schema_version": HUITUN_ACTIVITY_RESPONSE_SCHEMA_VERSION,
            "visibility_policy_version": HUITUN_ACTIVITY_VISIBILITY_POLICY_VERSION,
            "attempt_started_at": observed_at - timedelta(seconds=1),
            "observed_at": observed_at,
            "coverage_status": coverage_status,
            "last_publication_at": observed_at - timedelta(days=60),
            "latest_publication_id": "a" * 64,
            "coverage_end_at": observed_at,
            "request_ref": f"huitun-runtime-fixture:{observation_id}",
        },
    )
    return observation_id


def _insert_complete_observation(
    connection: Connection,
    *,
    account_id: UUID,
    identity_id: UUID,
    verification_id: UUID,
    response_schema_version: str = XHS_ACTIVITY_RESPONSE_SCHEMA_VERSION,
) -> UUID:
    observation_id = uuid4()
    observed_at = datetime(2026, 8, 23, tzinfo=UTC)
    connection.execute(
        text(
            """
            INSERT INTO content_activity_observations (
                id, platform_account_id, platform, schema_version, activity_semantics,
                provider_account_identity_id, provider_account_identity_verification_id,
                activity_source_provider, provider_product, endpoint, endpoint_version,
                adapter_version, capability_policy_version, response_schema_version,
                visibility_policy_version, attempt_started_at, observed_at,
                observation_status, coverage_status, activity_result, last_publication_at,
                latest_publication_id_namespace, latest_publication_id, latest_publication_type,
                co_latest_publication_count, normalized_timezone, scan_terminal_reason,
                scanned_page_count, scanned_item_count
            ) VALUES (
                :id, :platform_account_id, 'xiaohongshu'::platform_enum, 1,
                'CURRENT_PUBLIC_VISIBLE'::content_activity_semantics,
                :identity_id, :verification_id,
                'TIKHUB'::content_activity_provider, :provider_product,
                :endpoint, :endpoint_version, :adapter_version,
                :capability_policy_version, :response_schema_version, :visibility_policy_version,
                :attempt_started_at, :observed_at,
                'COMPLETE'::content_activity_observation_status,
                'FULL_CURRENT_PUBLIC_SET'::content_activity_coverage_status,
                'PUBLICATION_FOUND'::content_activity_result, :last_publication_at,
                'xiaohongshu.noteid', 'fixture-note',
                'VIDEO'::content_activity_publication_type, 1, 'UTC',
                'SINGLE_RESPONSE_COMPLETE'::content_activity_scan_terminal_reason, 1, 1
            )
            """
        ),
        {
            "id": observation_id,
            "platform_account_id": account_id,
            "identity_id": identity_id,
            "verification_id": verification_id,
            "provider_product": XHS_ACTIVITY_PRODUCT,
            "endpoint": XHS_ACTIVITY_ENDPOINT,
            "endpoint_version": XHS_ACTIVITY_ENDPOINT_VERSION,
            "adapter_version": XHS_ACTIVITY_ADAPTER_VERSION,
            "capability_policy_version": XHS_ACTIVITY_CAPABILITY_POLICY_VERSION,
            "response_schema_version": response_schema_version,
            "visibility_policy_version": XHS_ACTIVITY_VISIBILITY_POLICY_VERSION,
            "attempt_started_at": observed_at - timedelta(seconds=1),
            "observed_at": observed_at,
            "last_publication_at": observed_at - timedelta(days=10),
        },
    )
    return observation_id


def _insert_untrusted_observation(
    connection: Connection,
    *,
    account_id: UUID,
) -> UUID:
    observation_id = uuid4()
    observed_at = datetime(2026, 8, 23, 2, tzinfo=UTC)
    connection.execute(
        text(
            """
            INSERT INTO content_activity_observations (
                id, platform_account_id, platform, schema_version, activity_semantics,
                activity_source_provider, provider_product, endpoint, endpoint_version,
                adapter_version, capability_policy_version, response_schema_version,
                visibility_policy_version, attempt_started_at, observed_at,
                observation_status, coverage_status, activity_result, normalized_timezone,
                scan_terminal_reason, scanned_page_count, scanned_item_count,
                provider_error_class, provider_error_code
            ) VALUES (
                :id, :platform_account_id, 'xiaohongshu'::platform_enum, 1,
                'CURRENT_PUBLIC_VISIBLE'::content_activity_semantics,
                'TIKHUB'::content_activity_provider, 'XIAOHONGSHU_APP_V2',
                '/api/v1/xiaohongshu/app_v2/get_user_posted_notes', 'app_v2', 'adapter-v1',
                'TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1', 'schema-v1', 'visibility-v1',
                :attempt_started_at, :observed_at,
                'RESULT_UNTRUSTED'::content_activity_observation_status,
                'UNKNOWN'::content_activity_coverage_status,
                'UNDETERMINED'::content_activity_result, 'UTC',
                'POLICY_REJECTED'::content_activity_scan_terminal_reason, 0, 0,
                'POLICY_REJECTED'::content_activity_provider_error_class,
                'FIXTURE_UNTRUSTED'
            )
            """
        ),
        {
            "id": observation_id,
            "platform_account_id": account_id,
            "attempt_started_at": observed_at - timedelta(seconds=1),
            "observed_at": observed_at,
        },
    )
    return observation_id


def _insert_trusted_projection(
    connection: Connection,
    *,
    account_id: UUID,
    observation_id: UUID,
) -> UUID:
    projection_id = uuid4()
    observed_at = datetime(2026, 8, 23, tzinfo=UTC)
    connection.execute(
        text(
            """
            INSERT INTO content_activity_projections (
                id, platform_account_id, platform,
                latest_attempt_observation_id, latest_attempt_observed_at,
                latest_attempt_observation_status, latest_attempt_coverage_status,
                latest_attempt_activity_result, trusted_observation_id, trusted_observed_at,
                trusted_observation_status, trusted_coverage_status, trusted_activity_result,
                trusted_capability_policy_version, last_publication_at,
                latest_publication_id_namespace, latest_publication_id,
                latest_publication_type, co_latest_publication_count, version
            ) VALUES (
                :id, :platform_account_id, 'xiaohongshu'::platform_enum,
                :observation_id, :observed_at,
                'COMPLETE'::content_activity_observation_status,
                'FULL_CURRENT_PUBLIC_SET'::content_activity_coverage_status,
                'PUBLICATION_FOUND'::content_activity_result,
                :observation_id, :observed_at,
                'COMPLETE'::content_activity_observation_status,
                'FULL_CURRENT_PUBLIC_SET'::content_activity_coverage_status,
                'PUBLICATION_FOUND'::content_activity_result,
                'TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1', :last_publication_at,
                'xiaohongshu.noteid', 'fixture-note',
                'VIDEO'::content_activity_publication_type, 1, 1
            )
            """
        ),
        {
            "id": projection_id,
            "platform_account_id": account_id,
            "observation_id": observation_id,
            "observed_at": observed_at,
            "last_publication_at": observed_at - timedelta(days=10),
        },
    )
    return projection_id


def test_fresh_upgrade_physical_contract_and_alembic_check(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("head")
    migration_database.upgrade("head")
    migration_database.check()

    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0009_douyin_runtime_capture_v1"
        inspector = inspect(connection)
        assert CONTENT_ACTIVITY_TABLES <= set(inspector.get_table_names())
        assert _enum_values(connection, "provider_account_identity_namespace") == [
            "xiaohongshu.userid",
            "douyin.huitun_uid",
        ]
        assert _enum_values(connection, "content_activity_provider") == [
            "TIKHUB",
            "HUITUN_DOUYIN_AWEME_LIST",
        ]
        assert _enum_values(connection, "content_activity_semantics") == [
            "CURRENT_PUBLIC_VISIBLE",
            "HUITUN_RETURNED_SCOPE",
        ]
        assert _enum_values(connection, "content_activity_observation_status") == [
            "COMPLETE",
            "IDENTITY_UNRESOLVED",
            "ACCESS_RESTRICTED",
            "PROVIDER_AUTH_ERROR",
            "PROVIDER_RATE_LIMITED",
            "PROVIDER_ERROR",
            "RESULT_INCOMPLETE",
            "RESULT_UNTRUSTED",
            "UNKNOWN",
        ]
        assert _enum_values(connection, "content_activity_refresh_request_state") == [
            "PENDING",
            "RUNNING",
            "RETRY_WAIT",
            "SUCCEEDED",
            "FAILED",
            "CANCELLED",
        ]
        identity_unique = {
            item["name"] for item in inspector.get_unique_constraints("provider_account_identities")
        }
        assert {
            "uq_provider_account_identity_id_account_platform",
            "uq_provider_account_identity_external_owner",
        } <= identity_unique
        identity_indexes = {
            item["name"] for item in inspector.get_indexes("provider_account_identities")
        }
        assert {
            "uq_provider_account_identity_current_account_namespace",
            "ix_provider_account_identity_account_namespace_state",
        } <= identity_indexes
        observation_checks = {
            item["name"]
            for item in inspector.get_check_constraints("content_activity_observations")
        }
        assert {
            "ck_content_activity_observation_identity_pair",
            "ck_content_activity_observation_trusted_identity",
            "ck_content_activity_observation_status_tuple",
            "ck_content_activity_observation_publication_evidence",
        } <= observation_checks
        projection_fks = {
            item["name"] for item in inspector.get_foreign_keys("content_activity_projections")
        }
        assert {
            "fk_content_activity_projection_account_platform",
            "fk_content_activity_projection_latest_attempt",
            "fk_content_activity_projection_trusted_observation",
        } <= projection_fks
        refresh_request_fks = {
            item["name"] for item in inspector.get_foreign_keys("content_activity_refresh_requests")
        }
        assert {
            "fk_content_activity_refresh_request_account_platform",
            "fk_content_activity_refresh_request_requester",
            "fk_content_activity_refresh_request_last_observation",
        } <= refresh_request_fks
        refresh_request_indexes = {
            item["name"] for item in inspector.get_indexes("content_activity_refresh_requests")
        }
        assert {
            "uq_content_activity_refresh_request_active_account",
            "ix_content_activity_refresh_request_due",
            "ix_content_activity_refresh_request_expired_lease",
        } <= refresh_request_indexes
        triggers = set(
            connection.scalars(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND ("
                    "tgname LIKE 'trg_content_activity_%' "
                    "OR tgname LIKE 'trg_provider_account_identities_%' "
                    "OR tgname LIKE 'trg_provider_account_identity_verifications_%')"
                )
            )
        )
        assert {
            "trg_content_activity_observations_append_only",
            "trg_content_activity_observations_trusted_identity_guard",
            "trg_content_activity_projections_integrity_guard",
            "trg_provider_account_identities_lifecycle_guard",
            "trg_provider_account_identity_verifications_append_only",
        } <= triggers
        audit_actions = set(_enum_values(connection, "audit_action"))
        assert {
            "CONTENT_ACTIVITY_IDENTITY_VERIFIED",
            "CONTENT_ACTIVITY_IDENTITY_SUPERSEDED",
            "CONTENT_ACTIVITY_IDENTITY_CONFLICT",
            "CONTENT_ACTIVITY_REFRESH_REQUESTED",
            "CONTENT_ACTIVITY_REFRESH_COMPLETED",
            "CONTENT_ACTIVITY_REFRESH_FAILED",
        } <= audit_actions


def test_empty_migration_downgrade_removes_projection_guard_and_reupgrades(
    migration_database: MigrationDatabase,
) -> None:
    """The additive migration rolls back empty schema objects where practical."""

    migration_database.upgrade("head")
    migration_database.downgrade("0007_phase3a_persistence_amendment")

    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0007_phase3a_persistence_amendment"
        inspector = inspect(connection)
        assert not (CONTENT_ACTIVITY_TABLES & set(inspector.get_table_names()))
        functions = set(
            connection.scalars(
                text(
                    """
                    SELECT proname
                    FROM pg_proc
                    JOIN pg_namespace ON pg_namespace.oid = pg_proc.pronamespace
                    WHERE pg_namespace.nspname = current_schema()
                    """
                )
            )
        )
        assert {
            "guard_content_activity_projection_integrity",
            "guard_content_activity_observation_trusted_identity",
            "prevent_content_activity_observation_mutation",
        }.isdisjoint(functions)

    # Shared audit enum values are intentionally retained by PostgreSQL, so the
    # migration must also be re-upgrade-safe after a practical empty rollback.
    migration_database.upgrade("head")
    with migration_database.engine.connect() as connection:
        assert _revision(connection) == "0009_douyin_runtime_capture_v1"
        assert CONTENT_ACTIVITY_TABLES <= set(inspect(connection).get_table_names())


def test_identity_uniqueness_lifecycle_and_verification_append_only(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("head")
    with migration_database.engine.begin() as connection:
        _, account_id = _seed_account(connection, label="one")
        _, other_account_id = _seed_account(connection, label="two")
        identity_id, verification_id = _insert_identity(
            connection,
            account_id=account_id,
            external_identity="opaque-userid-1",
        )
        _assert_rejected(
            connection,
            """
            INSERT INTO provider_account_identities (
                id, platform_account_id, platform, namespace, opaque_external_identity,
                identity_source, resolver_contract_version, verification_state,
                resolved_at, verified_at, provenance_ref, lock_version
            ) VALUES (
                :id, :platform_account_id, 'xiaohongshu'::platform_enum,
                'xiaohongshu.userid'::provider_account_identity_namespace,
                'opaque-userid-1', 'TIKHUB_XHS_APP_V2', 'resolver-v1',
                'VERIFIED_CURRENT'::provider_account_identity_verification_state,
                :at, :at, 'fixture-provenance', 1
            )
            """,
            {
                "id": uuid4(),
                "platform_account_id": other_account_id,
                "at": datetime(2026, 8, 23, tzinfo=UTC),
            },
        )
        _assert_rejected(
            connection,
            "UPDATE provider_account_identities "
            "SET opaque_external_identity = 'overwrite' WHERE id = :id",
            {"id": identity_id},
        )
        _assert_rejected(
            connection,
            "UPDATE provider_account_identity_verifications "
            "SET provenance_ref = 'overwrite' WHERE id = :id",
            {"id": verification_id},
        )
        transition_at = datetime(2026, 8, 23, 1, tzinfo=UTC)
        connection.execute(
            text(
                "UPDATE provider_account_identities "
                "SET verification_state = 'SUPERSEDED', superseded_at = :superseded_at, "
                "lock_version = lock_version + 1 WHERE id = :id"
            ),
            {"id": identity_id, "superseded_at": transition_at},
        )
        assert (
            connection.scalar(
                text(
                    "SELECT verification_state::text "
                    "FROM provider_account_identities WHERE id = :id"
                ),
                {"id": identity_id},
            )
            == "SUPERSEDED"
        )
        _assert_rejected(
            connection,
            "UPDATE provider_account_identities "
            "SET verification_state = 'REVOKED', revoked_at = :revoked_at, "
            "lock_version = lock_version + 1 WHERE id = :id",
            {"id": identity_id, "revoked_at": transition_at + timedelta(seconds=1)},
        )


def test_observation_append_only_and_projection_account_reference(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("head")
    with migration_database.engine.begin() as connection:
        _, account_id = _seed_account(connection, label="observation")
        identity_id, verification_id = _insert_identity(
            connection,
            account_id=account_id,
            external_identity="opaque-userid-observation",
        )
        observation_id = _insert_complete_observation(
            connection,
            account_id=account_id,
            identity_id=identity_id,
            verification_id=verification_id,
        )
        _assert_rejected(
            connection,
            "UPDATE content_activity_observations SET scanned_item_count = 2 WHERE id = :id",
            {"id": observation_id},
        )
        _assert_rejected(
            connection,
            "DELETE FROM content_activity_observations WHERE id = :id",
            {"id": observation_id},
        )
        _assert_rejected(
            connection,
            """
            INSERT INTO content_activity_observations (
                id, platform_account_id, platform, schema_version, activity_semantics,
                activity_source_provider, provider_product, endpoint, endpoint_version,
                adapter_version, capability_policy_version, response_schema_version,
                visibility_policy_version, attempt_started_at, observed_at,
                observation_status, coverage_status, activity_result, normalized_timezone,
                scan_terminal_reason, scanned_page_count, scanned_item_count
            ) VALUES (
                :id, :platform_account_id, 'xiaohongshu'::platform_enum, 1,
                'CURRENT_PUBLIC_VISIBLE'::content_activity_semantics,
                'TIKHUB'::content_activity_provider, 'XIAOHONGSHU_APP_V2',
                '/api/v1/xiaohongshu/app_v2/get_user_posted_notes', 'app_v2', 'adapter-v1',
                'TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1', 'schema-v1', 'visibility-v1',
                :at, :at, 'COMPLETE'::content_activity_observation_status,
                'FULL_CURRENT_PUBLIC_SET'::content_activity_coverage_status,
                'NO_PUBLIC_CONTENT'::content_activity_result, 'UTC',
                'SINGLE_RESPONSE_COMPLETE'::content_activity_scan_terminal_reason, 1, 0
            )
            """,
            {
                "id": uuid4(),
                "platform_account_id": account_id,
                "at": datetime(2026, 8, 23, tzinfo=UTC),
            },
        )
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                _insert_complete_observation(
                    connection,
                    account_id=account_id,
                    identity_id=identity_id,
                    verification_id=verification_id,
                    response_schema_version="UNRECOGNIZED_XHS_RESPONSE_SCHEMA",
                )
        _, mismatched_account_id = _seed_account(connection, label="identity-contract-mismatch")
        mismatched_identity_id, mismatched_verification_id = _insert_identity(
            connection,
            account_id=mismatched_account_id,
            external_identity="opaque-userid-contract-mismatch",
            resolver_contract_version="UNRECOGNIZED_XHS_RESOLVER",
        )
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                _insert_complete_observation(
                    connection,
                    account_id=mismatched_account_id,
                    identity_id=mismatched_identity_id,
                    verification_id=mismatched_verification_id,
                )
        projection_id = _insert_trusted_projection(
            connection,
            account_id=account_id,
            observation_id=observation_id,
        )
        untrusted_observation_id = _insert_untrusted_observation(
            connection,
            account_id=account_id,
        )
        _assert_rejected(
            connection,
            """
            UPDATE content_activity_projections
            SET trusted_observation_id = :untrusted_observation_id,
                trusted_observed_at = :untrusted_observed_at,
                trusted_observation_status = 'COMPLETE'::content_activity_observation_status,
                trusted_coverage_status =
                    'FULL_CURRENT_PUBLIC_SET'::content_activity_coverage_status,
                trusted_activity_result = 'NO_PUBLIC_CONTENT'::content_activity_result,
                trusted_capability_policy_version = 'TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1',
                last_publication_at = NULL,
                latest_publication_id_namespace = NULL,
                latest_publication_id = NULL,
                latest_publication_type = NULL,
                co_latest_publication_count = NULL
            WHERE id = :id
            """,
            {
                "id": projection_id,
                "untrusted_observation_id": untrusted_observation_id,
                "untrusted_observed_at": datetime(2026, 8, 23, 2, tzinfo=UTC),
            },
        )
        superseded_at = datetime(2026, 8, 23, 1, tzinfo=UTC)
        connection.execute(
            text(
                "UPDATE provider_account_identities "
                "SET verification_state = 'SUPERSEDED', superseded_at = :superseded_at, "
                "lock_version = lock_version + 1 WHERE id = :id"
            ),
            {"id": identity_id, "superseded_at": superseded_at},
        )
        _assert_rejected(
            connection,
            """
            INSERT INTO content_activity_observations (
                id, platform_account_id, platform, schema_version, activity_semantics,
                provider_account_identity_id, provider_account_identity_verification_id,
                activity_source_provider, provider_product, endpoint, endpoint_version,
                adapter_version, capability_policy_version, response_schema_version,
                visibility_policy_version, attempt_started_at, observed_at,
                observation_status, coverage_status, activity_result, normalized_timezone,
                scan_terminal_reason, scanned_page_count, scanned_item_count
            ) VALUES (
                :id, :platform_account_id, 'xiaohongshu'::platform_enum, 1,
                'CURRENT_PUBLIC_VISIBLE'::content_activity_semantics,
                :identity_id, :verification_id,
                'TIKHUB'::content_activity_provider, 'XIAOHONGSHU_APP_V2',
                '/api/v1/xiaohongshu/app_v2/get_user_posted_notes', 'app_v2', 'adapter-v1',
                'TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1', 'schema-v1', 'visibility-v1',
                :at, :at, 'COMPLETE'::content_activity_observation_status,
                'FULL_CURRENT_PUBLIC_SET'::content_activity_coverage_status,
                'NO_PUBLIC_CONTENT'::content_activity_result, 'UTC',
                'SINGLE_RESPONSE_COMPLETE'::content_activity_scan_terminal_reason, 1, 0
            )
            """,
            {
                "id": uuid4(),
                "platform_account_id": account_id,
                "identity_id": identity_id,
                "verification_id": verification_id,
                "at": superseded_at + timedelta(seconds=1),
            },
        )
        _assert_rejected(
            connection,
            "UPDATE content_activity_projections SET version = version + 1 WHERE id = :id",
            {"id": projection_id},
        )
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                connection.execute(
                    text("DELETE FROM content_activity_projections WHERE id = :id"),
                    {"id": projection_id},
                )
                _insert_trusted_projection(
                    connection,
                    account_id=account_id,
                    observation_id=observation_id,
                )


def test_huitun_douyin_returned_scope_contract_is_complete_but_never_trusted_current(
    migration_database: MigrationDatabase,
) -> None:
    migration_database.upgrade("head")
    with migration_database.engine.begin() as connection:
        _, account_id = _seed_douyin_account(connection, label="returned-scope")
        identity_id, verification_id = _insert_huitun_douyin_identity(
            connection,
            account_id=account_id,
            external_uid="huitun-runtime-uid-1",
        )
        observation_id = _insert_complete_huitun_observation(
            connection,
            account_id=account_id,
            identity_id=identity_id,
            verification_id=verification_id,
        )
        assert connection.scalar(
            text(
                "SELECT activity_semantics::text FROM content_activity_observations "
                "WHERE id = :id"
            ),
            {"id": observation_id},
        ) == "HUITUN_RETURNED_SCOPE"
        # Returned-scope evidence is valid for the V1 business rule only. The
        # existing trusted-current projection guard must not accept it.
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                _insert_trusted_projection(
                    connection,
                    account_id=account_id,
                    observation_id=observation_id,
                )
        with pytest.raises(DBAPIError):
            with connection.begin_nested():
                _insert_complete_huitun_observation(
                    connection,
                    account_id=account_id,
                    identity_id=identity_id,
                    verification_id=verification_id,
                    coverage_status="FULL_CURRENT_PUBLIC_SET",
                )


def test_expired_running_lease_is_republished_and_recovers_to_retry_wait(
    migration_database: MigrationDatabase,
) -> None:
    """A worker crash cannot strand a leased request outside reconciliation."""

    migration_database.upgrade("head")
    request_id = uuid4()
    request_token = uuid4()
    lease_expired_at = datetime.now(UTC) - timedelta(minutes=5)

    with migration_database.engine.begin() as connection:
        _, account_id = _seed_account(connection, label="expired-running-lease")
        connection.execute(
            text(
                """
                INSERT INTO content_activity_refresh_requests (
                    id, platform_account_id, platform, request_token, idempotency_key,
                    state, attempt_count, max_attempts, next_attempt_at, lease_generation,
                    lease_expires_at, started_at, finished_at, last_observation_id,
                    last_error_code
                ) VALUES (
                    :id, :platform_account_id, 'xiaohongshu'::platform_enum,
                    :request_token, :idempotency_key,
                    'RUNNING'::content_activity_refresh_request_state,
                    1, 3, NULL, 1, :lease_expires_at, :started_at, NULL, NULL, NULL
                )
                """
            ),
            {
                "id": request_id,
                "platform_account_id": account_id,
                "request_token": request_token,
                "idempotency_key": f"expired-lease-{request_id.hex}",
                "lease_expires_at": lease_expired_at,
                "started_at": lease_expired_at - timedelta(seconds=1),
            },
        )

    async def reconcile_and_claim() -> None:
        engine = create_async_engine(migration_database.engine.url, pool_pre_ping=True)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                service = ContentActivityService(
                    session,
                    Settings(app_env="test", _env_file=None),
                )
                try:
                    # This path is database-only. Content Activity remains disabled,
                    # so no HTTP client or provider request can be constructed.
                    assert await service.reconcile_due_refresh_requests() == (request_token,)
                    result = await service.process_refresh_request(request_token)
                    assert not result.claimed
                    assert result.state is None
                    row = (
                        await session.execute(
                            text(
                                """
                                SELECT state::text, lease_expires_at, next_attempt_at,
                                       last_error_code, attempt_count
                                FROM content_activity_refresh_requests
                                WHERE id = :id
                                """
                            ),
                            {"id": request_id},
                        )
                    ).one()
                    assert row.state == "RETRY_WAIT"
                    assert row.lease_expires_at is None
                    assert row.next_attempt_at is not None
                    assert row.last_error_code == "LEASE_EXPIRED"
                    assert row.attempt_count == 1
                finally:
                    await service.aclose()
        finally:
            await engine.dispose()

    asyncio.run(reconcile_and_claim())
