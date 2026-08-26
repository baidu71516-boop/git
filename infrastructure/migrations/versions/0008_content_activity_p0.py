"""Add platform-neutral Content Activity identity, evidence, and request persistence.

Revision ID: 0008_content_activity_p0
Revises: 0007_phase3a_persistence_amendment
Create Date: 2026-08-23
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_content_activity_p0"
down_revision: str | None = "0007_phase3a_persistence_amendment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


provider_account_identity_namespace = postgresql.ENUM(
    "xiaohongshu.userid",
    name="provider_account_identity_namespace",
)
provider_account_identity_verification_state = postgresql.ENUM(
    "VERIFIED_CURRENT",
    "SUPERSEDED",
    "REVOKED",
    name="provider_account_identity_verification_state",
)
provider_account_identity_verification_outcome = postgresql.ENUM(
    "POLICY_VERIFIED",
    name="provider_account_identity_verification_outcome",
)
content_activity_provider = postgresql.ENUM("TIKHUB", name="content_activity_provider")
content_activity_semantics = postgresql.ENUM(
    "CURRENT_PUBLIC_VISIBLE",
    name="content_activity_semantics",
)
content_activity_observation_status = postgresql.ENUM(
    "COMPLETE",
    "IDENTITY_UNRESOLVED",
    "ACCESS_RESTRICTED",
    "PROVIDER_AUTH_ERROR",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_ERROR",
    "RESULT_INCOMPLETE",
    "RESULT_UNTRUSTED",
    "UNKNOWN",
    name="content_activity_observation_status",
)
content_activity_coverage_status = postgresql.ENUM(
    "FULL_CURRENT_PUBLIC_SET",
    "LATEST_BOUND_PROVEN",
    "LOOKBACK_BOUNDED",
    "INCOMPLETE",
    "UNKNOWN",
    name="content_activity_coverage_status",
)
content_activity_result = postgresql.ENUM(
    "PUBLICATION_FOUND",
    "NO_PUBLIC_CONTENT",
    "AT_LEAST_LOOKBACK_INACTIVE",
    "UNDETERMINED",
    name="content_activity_result",
)
content_activity_publication_type = postgresql.ENUM(
    "VIDEO",
    "IMAGE_TEXT",
    "OTHER",
    "UNKNOWN",
    name="content_activity_publication_type",
)
content_activity_provider_error_class = postgresql.ENUM(
    "INPUT_UNAVAILABLE",
    "TIMEOUT",
    "TRANSPORT",
    "AUTHENTICATION",
    "RATE_LIMITED",
    "SEMANTIC_FAILURE",
    "MALFORMED_RESPONSE",
    "SCHEMA_DRIFT",
    "IDENTITY_CONFLICT",
    "POLICY_REJECTED",
    "UNKNOWN",
    name="content_activity_provider_error_class",
)
content_activity_scan_terminal_reason = postgresql.ENUM(
    "SINGLE_RESPONSE_COMPLETE",
    "IDENTITY_UNRESOLVED",
    "HAS_MORE_TRUE",
    "PROVIDER_FAILURE",
    "RESPONSE_UNTRUSTED",
    "POLICY_REJECTED",
    "REQUEST_NOT_PERMITTED",
    "UNKNOWN",
    name="content_activity_scan_terminal_reason",
)
content_activity_refresh_request_state = postgresql.ENUM(
    "PENDING",
    "RUNNING",
    "RETRY_WAIT",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    name="content_activity_refresh_request_state",
)

AUDIT_ACTIONS = (
    "CONTENT_ACTIVITY_IDENTITY_VERIFIED",
    "CONTENT_ACTIVITY_IDENTITY_SUPERSEDED",
    "CONTENT_ACTIVITY_IDENTITY_CONFLICT",
    "CONTENT_ACTIVITY_REFRESH_REQUESTED",
    "CONTENT_ACTIVITY_REFRESH_COMPLETED",
    "CONTENT_ACTIVITY_REFRESH_FAILED",
)

XHS_IDENTITY_SOURCE = "TIKHUB_XHS_APP_V2"
XHS_IDENTITY_RESOLVER_CONTRACT_VERSION = "TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1"
XHS_ACTIVITY_PROVIDER = "TIKHUB"
XHS_ACTIVITY_PRODUCT = "XIAOHONGSHU_APP_V2"
XHS_ACTIVITY_ENDPOINT = "/api/v1/xiaohongshu/app_v2/get_user_posted_notes"
XHS_ACTIVITY_ENDPOINT_VERSION = "APP_V2"
XHS_ACTIVITY_ADAPTER_VERSION = "TIKHUB_XHS_CONTENT_ACTIVITY_ADAPTER_V1"
XHS_ACTIVITY_CAPABILITY_POLICY_VERSION = "TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1"
XHS_ACTIVITY_RESPONSE_SCHEMA_VERSION = "TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1"
XHS_ACTIVITY_VISIBILITY_POLICY_VERSION = "TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1"


def _timestamp_columns() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def _enum_column(name: str, enum_name: str, *, nullable: bool = False) -> sa.Column[sa.Enum]:
    return sa.Column(
        name,
        postgresql.ENUM(name=enum_name, create_type=False),
        nullable=nullable,
    )


def _preflight_downgrade() -> None:
    has_evidence = bool(
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT EXISTS (SELECT 1 FROM provider_account_identities)
                    OR EXISTS (SELECT 1 FROM provider_account_identity_verifications)
                    OR EXISTS (SELECT 1 FROM content_activity_observations)
                    OR EXISTS (SELECT 1 FROM content_activity_projections)
                    OR EXISTS (SELECT 1 FROM content_activity_refresh_requests)
                """
            )
        )
        .scalar_one()
    )
    if has_evidence:
        raise RuntimeError(
            "0008 downgrade blocked: Content Activity identity, evidence, projection, or request "
            "records cannot be destroyed"
        )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for action in AUDIT_ACTIONS:
            op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{action}'")

    bind = op.get_bind()
    for enum_type in (
        provider_account_identity_namespace,
        provider_account_identity_verification_state,
        provider_account_identity_verification_outcome,
        content_activity_provider,
        content_activity_semantics,
        content_activity_observation_status,
        content_activity_coverage_status,
        content_activity_result,
        content_activity_publication_type,
        content_activity_provider_error_class,
        content_activity_scan_terminal_reason,
        content_activity_refresh_request_state,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "provider_account_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("platform_account_id", sa.Uuid(), nullable=False),
        _enum_column("platform", "platform_enum"),
        _enum_column("namespace", "provider_account_identity_namespace"),
        sa.Column("opaque_external_identity", sa.String(length=512), nullable=False),
        sa.Column("identity_source", sa.String(length=120), nullable=False),
        sa.Column("resolver_contract_version", sa.String(length=160), nullable=False),
        _enum_column("verification_state", "provider_account_identity_verification_state"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provenance_ref", sa.String(length=160), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "length(trim(opaque_external_identity)) BETWEEN 1 AND 512",
            name="ck_provider_account_identity_opaque_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(identity_source)) BETWEEN 1 AND 120",
            name="ck_provider_account_identity_source_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(resolver_contract_version)) BETWEEN 1 AND 160",
            name="ck_provider_account_identity_resolver_version_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(provenance_ref)) BETWEEN 1 AND 160",
            name="ck_provider_account_identity_provenance_nonblank",
        ),
        sa.CheckConstraint("lock_version >= 1", name="ck_provider_account_identity_lock_version"),
        sa.CheckConstraint(
            "platform = 'xiaohongshu' AND namespace = 'xiaohongshu.userid'",
            name="ck_provider_account_identity_platform_namespace",
        ),
        sa.CheckConstraint(
            "resolved_at <= verified_at",
            name="ck_provider_account_identity_resolution_before_verification",
        ),
        sa.CheckConstraint(
            "(verification_state = 'VERIFIED_CURRENT' AND superseded_at IS NULL "
            "AND revoked_at IS NULL) OR "
            "(verification_state = 'SUPERSEDED' AND superseded_at IS NOT NULL "
            "AND revoked_at IS NULL AND superseded_at >= verified_at) OR "
            "(verification_state = 'REVOKED' AND revoked_at IS NOT NULL "
            "AND superseded_at IS NULL AND revoked_at >= verified_at)",
            name="ck_provider_account_identity_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["platform_account_id", "platform"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.platform"],
            name="fk_provider_account_identity_account_platform",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "platform_account_id",
            "platform",
            name="uq_provider_account_identity_id_account_platform",
        ),
        sa.UniqueConstraint(
            "platform",
            "namespace",
            "opaque_external_identity",
            name="uq_provider_account_identity_external_owner",
        ),
    )
    op.create_index(
        "uq_provider_account_identity_current_account_namespace",
        "provider_account_identities",
        ["platform_account_id", "namespace"],
        unique=True,
        postgresql_where=sa.text("verification_state = 'VERIFIED_CURRENT'"),
    )
    op.create_index(
        "ix_provider_account_identity_account_namespace_state",
        "provider_account_identities",
        ["platform_account_id", "namespace", "verification_state"],
    )

    op.create_table(
        "provider_account_identity_verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_account_identity_id", sa.Uuid(), nullable=False),
        sa.Column("identity_source", sa.String(length=120), nullable=False),
        sa.Column("resolver_contract_version", sa.String(length=160), nullable=False),
        _enum_column(
            "verification_outcome",
            "provider_account_identity_verification_outcome",
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provenance_ref", sa.String(length=160), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "length(trim(identity_source)) BETWEEN 1 AND 120",
            name="ck_provider_identity_verification_source_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(resolver_contract_version)) BETWEEN 1 AND 160",
            name="ck_provider_identity_verification_resolver_version_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(provenance_ref)) BETWEEN 1 AND 160",
            name="ck_provider_identity_verification_provenance_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(idempotency_key)) BETWEEN 1 AND 255",
            name="ck_provider_identity_verification_idempotency_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["provider_account_identity_id"],
            ["provider_account_identities.id"],
            name="fk_provider_account_identity_verification_binding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "provider_account_identity_id",
            name="uq_provider_account_identity_verification_id_binding",
        ),
        sa.UniqueConstraint(
            "identity_source",
            "resolver_contract_version",
            "idempotency_key",
            name="uq_provider_account_identity_verification_idempotency",
        ),
    )
    op.create_index(
        "ix_provider_account_identity_verification_history",
        "provider_account_identity_verifications",
        ["provider_account_identity_id", "verified_at", "id"],
    )

    op.create_table(
        "content_activity_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("platform_account_id", sa.Uuid(), nullable=False),
        _enum_column("platform", "platform_enum"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        _enum_column("activity_semantics", "content_activity_semantics"),
        sa.Column("provider_account_identity_id", sa.Uuid(), nullable=True),
        sa.Column("provider_account_identity_verification_id", sa.Uuid(), nullable=True),
        _enum_column("activity_source_provider", "content_activity_provider"),
        sa.Column("provider_product", sa.String(length=120), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("endpoint_version", sa.String(length=120), nullable=False),
        sa.Column("adapter_version", sa.String(length=160), nullable=False),
        sa.Column("capability_policy_version", sa.String(length=160), nullable=False),
        sa.Column("response_schema_version", sa.String(length=160), nullable=False),
        sa.Column("visibility_policy_version", sa.String(length=160), nullable=False),
        sa.Column("attempt_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        _enum_column("observation_status", "content_activity_observation_status"),
        _enum_column("coverage_status", "content_activity_coverage_status"),
        _enum_column("activity_result", "content_activity_result"),
        sa.Column("last_publication_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_publication_id_namespace", sa.String(length=120), nullable=True),
        sa.Column("latest_publication_id", sa.String(length=512), nullable=True),
        _enum_column("latest_publication_type", "content_activity_publication_type", nullable=True),
        sa.Column("co_latest_publication_count", sa.Integer(), nullable=True),
        sa.Column("coverage_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("coverage_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timestamp_encoding", sa.String(length=40), nullable=True),
        sa.Column("source_timezone", sa.String(length=64), nullable=True),
        sa.Column("timezone_basis", sa.String(length=80), nullable=True),
        sa.Column(
            "normalized_timezone", sa.String(length=32), nullable=False, server_default="UTC"
        ),
        sa.Column("request_ref", sa.String(length=160), nullable=True),
        sa.Column("provenance_ref", sa.String(length=160), nullable=True),
        _enum_column("scan_terminal_reason", "content_activity_scan_terminal_reason"),
        sa.Column("scanned_page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scanned_item_count", sa.Integer(), nullable=False, server_default="0"),
        _enum_column(
            "provider_error_class",
            "content_activity_provider_error_class",
            nullable=True,
        ),
        sa.Column("provider_error_code", sa.String(length=80), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_content_activity_observation_schema_version"
        ),
        sa.CheckConstraint(
            "(provider_account_identity_id IS NULL "
            "AND provider_account_identity_verification_id IS NULL) OR "
            "(provider_account_identity_id IS NOT NULL "
            "AND provider_account_identity_verification_id IS NOT NULL)",
            name="ck_content_activity_observation_identity_pair",
        ),
        sa.CheckConstraint(
            "observation_status <> 'COMPLETE' "
            "OR (provider_account_identity_id IS NOT NULL "
            "AND provider_account_identity_verification_id IS NOT NULL)",
            name="ck_content_activity_observation_trusted_identity",
        ),
        sa.CheckConstraint(
            "attempt_started_at <= observed_at",
            name="ck_content_activity_observation_attempt_range",
        ),
        sa.CheckConstraint(
            "coverage_start_at IS NULL OR coverage_end_at IS NULL "
            "OR coverage_start_at <= coverage_end_at",
            name="ck_content_activity_observation_coverage_range",
        ),
        sa.CheckConstraint(
            "last_publication_at IS NULL OR last_publication_at <= observed_at",
            name="ck_content_activity_observation_publication_not_future",
        ),
        sa.CheckConstraint(
            "scanned_page_count >= 0 AND scanned_item_count >= 0",
            name="ck_content_activity_observation_scan_counts",
        ),
        sa.CheckConstraint(
            "co_latest_publication_count IS NULL OR co_latest_publication_count >= 1",
            name="ck_content_activity_observation_co_latest_count",
        ),
        sa.CheckConstraint(
            "normalized_timezone = 'UTC'",
            name="ck_content_activity_observation_normalized_timezone",
        ),
        sa.CheckConstraint(
            "length(trim(provider_product)) BETWEEN 1 AND 120 AND "
            "length(trim(endpoint)) BETWEEN 1 AND 255 AND "
            "length(trim(endpoint_version)) BETWEEN 1 AND 120 AND "
            "length(trim(adapter_version)) BETWEEN 1 AND 160 AND "
            "length(trim(capability_policy_version)) BETWEEN 1 AND 160 AND "
            "length(trim(response_schema_version)) BETWEEN 1 AND 160 AND "
            "length(trim(visibility_policy_version)) BETWEEN 1 AND 160",
            name="ck_content_activity_observation_contract_versions",
        ),
        sa.CheckConstraint(
            "(request_ref IS NULL OR length(trim(request_ref)) BETWEEN 1 AND 160) AND "
            "(provenance_ref IS NULL OR length(trim(provenance_ref)) BETWEEN 1 AND 160) AND "
            "(provider_error_code IS NULL OR length(trim(provider_error_code)) BETWEEN 1 AND 80)",
            name="ck_content_activity_observation_sanitized_refs",
        ),
        sa.CheckConstraint(
            "(activity_result = 'PUBLICATION_FOUND' "
            "AND last_publication_at IS NOT NULL "
            "AND latest_publication_id_namespace IS NOT NULL "
            "AND latest_publication_id IS NOT NULL "
            "AND latest_publication_type IS NOT NULL "
            "AND co_latest_publication_count IS NOT NULL) OR "
            "(activity_result <> 'PUBLICATION_FOUND' "
            "AND last_publication_at IS NULL "
            "AND latest_publication_id_namespace IS NULL "
            "AND latest_publication_id IS NULL "
            "AND latest_publication_type IS NULL "
            "AND co_latest_publication_count IS NULL)",
            name="ck_content_activity_observation_publication_evidence",
        ),
        sa.CheckConstraint(
            "(observation_status = 'COMPLETE' AND ("
            "(coverage_status IN ('FULL_CURRENT_PUBLIC_SET', 'LATEST_BOUND_PROVEN') "
            "AND activity_result = 'PUBLICATION_FOUND') OR "
            "(coverage_status = 'FULL_CURRENT_PUBLIC_SET' "
            "AND activity_result = 'NO_PUBLIC_CONTENT') OR "
            "(coverage_status = 'LOOKBACK_BOUNDED' "
            "AND activity_result = 'AT_LEAST_LOOKBACK_INACTIVE'))) OR "
            "(observation_status <> 'COMPLETE' "
            "AND coverage_status IN ('INCOMPLETE', 'UNKNOWN') "
            "AND activity_result = 'UNDETERMINED')",
            name="ck_content_activity_observation_status_tuple",
        ),
        sa.ForeignKeyConstraint(
            ["platform_account_id", "platform"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.platform"],
            name="fk_content_activity_observation_account_platform",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_account_identity_id", "platform_account_id", "platform"],
            [
                "provider_account_identities.id",
                "provider_account_identities.platform_account_id",
                "provider_account_identities.platform",
            ],
            name="fk_content_activity_observation_identity_account_platform",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_account_identity_verification_id", "provider_account_identity_id"],
            [
                "provider_account_identity_verifications.id",
                "provider_account_identity_verifications.provider_account_identity_id",
            ],
            name="fk_content_activity_observation_identity_verification",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "platform_account_id",
            "platform",
            name="uq_content_activity_observation_id_account_platform",
        ),
    )
    op.create_index(
        "ix_content_activity_observation_account_history",
        "content_activity_observations",
        ["platform_account_id", "observed_at", "id"],
    )

    op.create_table(
        "content_activity_projections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("platform_account_id", sa.Uuid(), nullable=False),
        _enum_column("platform", "platform_enum"),
        sa.Column("latest_attempt_observation_id", sa.Uuid(), nullable=False),
        sa.Column("latest_attempt_observed_at", sa.DateTime(timezone=True), nullable=False),
        _enum_column("latest_attempt_observation_status", "content_activity_observation_status"),
        _enum_column("latest_attempt_coverage_status", "content_activity_coverage_status"),
        _enum_column("latest_attempt_activity_result", "content_activity_result"),
        _enum_column(
            "latest_attempt_provider_error_class",
            "content_activity_provider_error_class",
            nullable=True,
        ),
        sa.Column("latest_attempt_provider_error_code", sa.String(length=80), nullable=True),
        sa.Column("trusted_observation_id", sa.Uuid(), nullable=True),
        sa.Column("trusted_observed_at", sa.DateTime(timezone=True), nullable=True),
        _enum_column(
            "trusted_observation_status",
            "content_activity_observation_status",
            nullable=True,
        ),
        _enum_column(
            "trusted_coverage_status",
            "content_activity_coverage_status",
            nullable=True,
        ),
        _enum_column("trusted_activity_result", "content_activity_result", nullable=True),
        sa.Column("trusted_capability_policy_version", sa.String(length=160), nullable=True),
        sa.Column("last_publication_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_publication_id_namespace", sa.String(length=120), nullable=True),
        sa.Column("latest_publication_id", sa.String(length=512), nullable=True),
        _enum_column("latest_publication_type", "content_activity_publication_type", nullable=True),
        sa.Column("co_latest_publication_count", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamp_columns(),
        sa.CheckConstraint("version >= 1", name="ck_content_activity_projection_version"),
        sa.CheckConstraint(
            "(latest_attempt_provider_error_code IS NULL OR "
            "length(trim(latest_attempt_provider_error_code)) BETWEEN 1 AND 80)",
            name="ck_content_activity_projection_latest_attempt_error",
        ),
        sa.CheckConstraint(
            "(trusted_observation_id IS NULL AND trusted_observed_at IS NULL "
            "AND trusted_observation_status IS NULL AND trusted_coverage_status IS NULL "
            "AND trusted_activity_result IS NULL AND trusted_capability_policy_version IS NULL "
            "AND last_publication_at IS NULL AND latest_publication_id_namespace IS NULL "
            "AND latest_publication_id IS NULL AND latest_publication_type IS NULL "
            "AND co_latest_publication_count IS NULL) OR "
            "(trusted_observation_id IS NOT NULL AND trusted_observed_at IS NOT NULL "
            "AND trusted_observation_status = 'COMPLETE' "
            "AND trusted_coverage_status IS NOT NULL AND trusted_activity_result IS NOT NULL "
            "AND length(trim(trusted_capability_policy_version)) BETWEEN 1 AND 160)",
            name="ck_content_activity_projection_trusted_shape",
        ),
        sa.CheckConstraint(
            "(trusted_activity_result = 'PUBLICATION_FOUND' "
            "AND last_publication_at IS NOT NULL "
            "AND latest_publication_id_namespace IS NOT NULL "
            "AND latest_publication_id IS NOT NULL "
            "AND latest_publication_type IS NOT NULL "
            "AND co_latest_publication_count IS NOT NULL) OR "
            "(trusted_activity_result IS NULL OR "
            "(trusted_activity_result <> 'PUBLICATION_FOUND' "
            "AND last_publication_at IS NULL "
            "AND latest_publication_id_namespace IS NULL "
            "AND latest_publication_id IS NULL "
            "AND latest_publication_type IS NULL "
            "AND co_latest_publication_count IS NULL))",
            name="ck_content_activity_projection_publication_evidence",
        ),
        sa.CheckConstraint(
            "co_latest_publication_count IS NULL OR co_latest_publication_count >= 1",
            name="ck_content_activity_projection_co_latest_count",
        ),
        sa.ForeignKeyConstraint(
            ["platform_account_id", "platform"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.platform"],
            name="fk_content_activity_projection_account_platform",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["latest_attempt_observation_id", "platform_account_id", "platform"],
            [
                "content_activity_observations.id",
                "content_activity_observations.platform_account_id",
                "content_activity_observations.platform",
            ],
            name="fk_content_activity_projection_latest_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trusted_observation_id", "platform_account_id", "platform"],
            [
                "content_activity_observations.id",
                "content_activity_observations.platform_account_id",
                "content_activity_observations.platform",
            ],
            name="fk_content_activity_projection_trusted_observation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_account_id", name="uq_content_activity_projection_account"),
    )
    op.create_index(
        "ix_content_activity_projection_trusted_publication",
        "content_activity_projections",
        ["platform", "last_publication_at", "trusted_observed_at", "platform_account_id"],
        postgresql_where=sa.text(
            "trusted_observation_id IS NOT NULL AND trusted_activity_result = 'PUBLICATION_FOUND'"
        ),
    )
    op.create_index(
        "ix_content_activity_projection_latest_attempt",
        "content_activity_projections",
        ["latest_attempt_observed_at", "platform_account_id"],
    )

    op.create_table(
        "content_activity_refresh_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("platform_account_id", sa.Uuid(), nullable=False),
        _enum_column("platform", "platform_enum"),
        sa.Column("requested_by_operator_id", sa.Uuid(), nullable=True),
        sa.Column("request_token", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        _enum_column("state", "content_activity_refresh_request_state"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_observation_id", sa.Uuid(), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="ck_content_activity_refresh_request_attempt_bounds",
        ),
        sa.CheckConstraint(
            "lease_generation >= 0",
            name="ck_content_activity_refresh_request_lease_generation",
        ),
        sa.CheckConstraint(
            "length(trim(idempotency_key)) BETWEEN 1 AND 255",
            name="ck_content_activity_refresh_request_idempotency_nonblank",
        ),
        sa.CheckConstraint(
            "(last_error_code IS NULL OR length(trim(last_error_code)) BETWEEN 1 AND 80)",
            name="ck_content_activity_refresh_request_error_code",
        ),
        sa.CheckConstraint(
            "(state IN ('PENDING', 'RETRY_WAIT') AND finished_at IS NULL "
            "AND lease_expires_at IS NULL AND next_attempt_at IS NOT NULL) OR "
            "(state = 'RUNNING' AND finished_at IS NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(state IN ('SUCCEEDED', 'FAILED', 'CANCELLED') "
            "AND finished_at IS NOT NULL AND lease_expires_at IS NULL)",
            name="ck_content_activity_refresh_request_lifecycle",
        ),
        sa.CheckConstraint(
            "state <> 'SUCCEEDED' OR last_observation_id IS NOT NULL",
            name="ck_content_activity_refresh_request_success_observation",
        ),
        sa.ForeignKeyConstraint(
            ["platform_account_id", "platform"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.platform"],
            name="fk_content_activity_refresh_request_account_platform",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_operator_id"],
            ["operators.id"],
            name="fk_content_activity_refresh_request_requester",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_observation_id", "platform_account_id", "platform"],
            [
                "content_activity_observations.id",
                "content_activity_observations.platform_account_id",
                "content_activity_observations.platform",
            ],
            name="fk_content_activity_refresh_request_last_observation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_token", name="uq_content_activity_refresh_request_token"),
        sa.UniqueConstraint(
            "platform_account_id",
            "idempotency_key",
            name="uq_content_activity_refresh_request_account_idempotency",
        ),
    )
    op.create_index(
        "uq_content_activity_refresh_request_active_account",
        "content_activity_refresh_requests",
        ["platform_account_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('PENDING', 'RUNNING', 'RETRY_WAIT')"),
    )
    op.create_index(
        "ix_content_activity_refresh_request_due",
        "content_activity_refresh_requests",
        ["state", "next_attempt_at", "created_at", "id"],
        postgresql_where=sa.text("state IN ('PENDING', 'RETRY_WAIT')"),
    )
    op.create_index(
        "ix_content_activity_refresh_request_expired_lease",
        "content_activity_refresh_requests",
        ["lease_expires_at", "id"],
        postgresql_where=sa.text("state = 'RUNNING'"),
    )

    op.execute(
        """
        CREATE FUNCTION prevent_provider_account_identity_verification_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'provider_account_identity_verifications is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_provider_account_identity_verifications_append_only "
        "BEFORE UPDATE OR DELETE ON provider_account_identity_verifications FOR EACH ROW "
        "EXECUTE FUNCTION prevent_provider_account_identity_verification_mutation()"
    )
    op.execute(
        """
        CREATE FUNCTION prevent_content_activity_observation_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'content_activity_observations is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_content_activity_observations_append_only "
        "BEFORE UPDATE OR DELETE ON content_activity_observations FOR EACH ROW "
        "EXECUTE FUNCTION prevent_content_activity_observation_mutation()"
    )
    op.execute(
        f"""
        CREATE FUNCTION guard_content_activity_observation_trusted_identity()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.observation_status <> 'COMPLETE' THEN
                RETURN NEW;
            END IF;
            IF NEW.schema_version <> 1
                OR NEW.activity_semantics <> 'CURRENT_PUBLIC_VISIBLE'
                OR NEW.activity_source_provider <> '{XHS_ACTIVITY_PROVIDER}'
                OR NEW.provider_product <> '{XHS_ACTIVITY_PRODUCT}'
                OR NEW.endpoint <> '{XHS_ACTIVITY_ENDPOINT}'
                OR NEW.endpoint_version <> '{XHS_ACTIVITY_ENDPOINT_VERSION}'
                OR NEW.adapter_version <> '{XHS_ACTIVITY_ADAPTER_VERSION}'
                OR NEW.capability_policy_version <> '{XHS_ACTIVITY_CAPABILITY_POLICY_VERSION}'
                OR NEW.response_schema_version <> '{XHS_ACTIVITY_RESPONSE_SCHEMA_VERSION}'
                OR NEW.visibility_policy_version <> '{XHS_ACTIVITY_VISIBILITY_POLICY_VERSION}'
                OR NEW.coverage_status <> 'FULL_CURRENT_PUBLIC_SET'
                OR NEW.activity_result NOT IN ('PUBLICATION_FOUND', 'NO_PUBLIC_CONTENT')
                OR NEW.scan_terminal_reason <> 'SINGLE_RESPONSE_COMPLETE'
                OR NEW.scanned_page_count <> 1
                OR NEW.provider_error_class IS NOT NULL
                OR NEW.provider_error_code IS NOT NULL
                OR (
                    NEW.activity_result = 'PUBLICATION_FOUND'
                    AND (
                        NEW.latest_publication_type NOT IN ('VIDEO', 'IMAGE_TEXT')
                        OR NEW.scanned_item_count < 1
                    )
                ) THEN
                RAISE EXCEPTION 'COMPLETE observation violates D1A capability contract';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM provider_account_identities AS identity_binding
                JOIN provider_account_identity_verifications AS verification_event
                    ON verification_event.provider_account_identity_id = identity_binding.id
                WHERE identity_binding.id = NEW.provider_account_identity_id
                    AND identity_binding.platform_account_id = NEW.platform_account_id
                    AND identity_binding.platform = NEW.platform
                    AND identity_binding.verification_state = 'VERIFIED_CURRENT'
                    AND identity_binding.identity_source = '{XHS_IDENTITY_SOURCE}'
                    AND identity_binding.resolver_contract_version =
                        '{XHS_IDENTITY_RESOLVER_CONTRACT_VERSION}'
                    AND identity_binding.verified_at <= NEW.observed_at
                    AND verification_event.id = NEW.provider_account_identity_verification_id
                    AND verification_event.identity_source = '{XHS_IDENTITY_SOURCE}'
                    AND verification_event.resolver_contract_version =
                        '{XHS_IDENTITY_RESOLVER_CONTRACT_VERSION}'
                    AND verification_event.verification_outcome = 'POLICY_VERIFIED'
                    AND verification_event.verified_at <= NEW.observed_at
            ) THEN
                RAISE EXCEPTION
                    'COMPLETE observation requires current verified identity';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_content_activity_observations_trusted_identity_guard "
        "BEFORE INSERT ON content_activity_observations FOR EACH ROW "
        "EXECUTE FUNCTION guard_content_activity_observation_trusted_identity()"
    )
    op.execute(
        f"""
        CREATE FUNCTION guard_content_activity_projection_integrity()
        RETURNS trigger AS $$
        DECLARE
            latest_observation content_activity_observations%ROWTYPE;
            trusted_observation content_activity_observations%ROWTYPE;
        BEGIN
            SELECT * INTO latest_observation
            FROM content_activity_observations
            WHERE id = NEW.latest_attempt_observation_id
                AND platform_account_id = NEW.platform_account_id
                AND platform = NEW.platform;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'latest attempt observation must belong to projection account';
            END IF;

            IF NEW.latest_attempt_observed_at IS DISTINCT FROM latest_observation.observed_at
                OR NEW.latest_attempt_observation_status
                    IS DISTINCT FROM latest_observation.observation_status
                OR NEW.latest_attempt_coverage_status
                    IS DISTINCT FROM latest_observation.coverage_status
                OR NEW.latest_attempt_activity_result
                    IS DISTINCT FROM latest_observation.activity_result
                OR NEW.latest_attempt_provider_error_class
                    IS DISTINCT FROM latest_observation.provider_error_class
                OR NEW.latest_attempt_provider_error_code
                    IS DISTINCT FROM latest_observation.provider_error_code THEN
                RAISE EXCEPTION 'latest attempt projection fields must mirror observation';
            END IF;

            IF NEW.trusted_observation_id IS NULL THEN
                RETURN NEW;
            END IF;

            SELECT * INTO trusted_observation
            FROM content_activity_observations
            WHERE id = NEW.trusted_observation_id
                AND platform_account_id = NEW.platform_account_id
                AND platform = NEW.platform;

            IF NOT FOUND
                OR trusted_observation.observation_status <> 'COMPLETE'
                OR trusted_observation.coverage_status <> 'FULL_CURRENT_PUBLIC_SET'
                OR trusted_observation.activity_result
                    NOT IN ('PUBLICATION_FOUND', 'NO_PUBLIC_CONTENT')
                OR trusted_observation.provider_account_identity_id IS NULL
                OR trusted_observation.provider_account_identity_verification_id IS NULL THEN
                RAISE EXCEPTION 'trusted projection requires a complete trusted observation';
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM provider_account_identities AS identity_binding
                JOIN provider_account_identity_verifications AS verification_event
                    ON verification_event.provider_account_identity_id = identity_binding.id
                WHERE identity_binding.id = trusted_observation.provider_account_identity_id
                    AND identity_binding.platform_account_id = NEW.platform_account_id
                    AND identity_binding.platform = NEW.platform
                    AND identity_binding.verification_state = 'VERIFIED_CURRENT'
                    AND identity_binding.identity_source = '{XHS_IDENTITY_SOURCE}'
                    AND identity_binding.resolver_contract_version =
                        '{XHS_IDENTITY_RESOLVER_CONTRACT_VERSION}'
                    AND identity_binding.verified_at <= trusted_observation.observed_at
                    AND verification_event.id =
                        trusted_observation.provider_account_identity_verification_id
                    AND verification_event.identity_source = '{XHS_IDENTITY_SOURCE}'
                    AND verification_event.resolver_contract_version =
                        '{XHS_IDENTITY_RESOLVER_CONTRACT_VERSION}'
                    AND verification_event.verification_outcome = 'POLICY_VERIFIED'
                    AND verification_event.verified_at <= trusted_observation.observed_at
            ) THEN
                RAISE EXCEPTION
                    'trusted projection requires a current verified D1A identity';
            END IF;

            IF NEW.trusted_observed_at IS DISTINCT FROM trusted_observation.observed_at
                OR NEW.trusted_observation_status
                    IS DISTINCT FROM trusted_observation.observation_status
                OR NEW.trusted_coverage_status
                    IS DISTINCT FROM trusted_observation.coverage_status
                OR NEW.trusted_activity_result
                    IS DISTINCT FROM trusted_observation.activity_result
                OR NEW.trusted_capability_policy_version
                    IS DISTINCT FROM trusted_observation.capability_policy_version
                OR NEW.last_publication_at
                    IS DISTINCT FROM trusted_observation.last_publication_at
                OR NEW.latest_publication_id_namespace
                    IS DISTINCT FROM trusted_observation.latest_publication_id_namespace
                OR NEW.latest_publication_id
                    IS DISTINCT FROM trusted_observation.latest_publication_id
                OR NEW.latest_publication_type
                    IS DISTINCT FROM trusted_observation.latest_publication_type
                OR NEW.co_latest_publication_count
                    IS DISTINCT FROM trusted_observation.co_latest_publication_count THEN
                RAISE EXCEPTION 'trusted projection fields must mirror trusted observation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_content_activity_projections_integrity_guard "
        "BEFORE INSERT OR UPDATE ON content_activity_projections FOR EACH ROW "
        "EXECUTE FUNCTION guard_content_activity_projection_integrity()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_provider_account_identity_lifecycle() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'provider_account_identities cannot be deleted';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.platform_account_id IS DISTINCT FROM OLD.platform_account_id
                OR NEW.platform IS DISTINCT FROM OLD.platform
                OR NEW.namespace IS DISTINCT FROM OLD.namespace
                OR NEW.opaque_external_identity IS DISTINCT FROM OLD.opaque_external_identity
                OR NEW.identity_source IS DISTINCT FROM OLD.identity_source
                OR NEW.resolver_contract_version IS DISTINCT FROM OLD.resolver_contract_version
                OR NEW.resolved_at IS DISTINCT FROM OLD.resolved_at
                OR NEW.verified_at IS DISTINCT FROM OLD.verified_at
                OR NEW.provenance_ref IS DISTINCT FROM OLD.provenance_ref
                OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'provider_account_identities immutable fields cannot change';
            END IF;
            IF OLD.verification_state <> 'VERIFIED_CURRENT'
                OR NEW.verification_state NOT IN ('SUPERSEDED', 'REVOKED')
                OR NEW.lock_version <> OLD.lock_version + 1 THEN
                RAISE EXCEPTION
                    'provider_account_identities require a CAS terminal lifecycle transition';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_provider_account_identities_lifecycle_guard "
        "BEFORE UPDATE OR DELETE ON provider_account_identities FOR EACH ROW "
        "EXECUTE FUNCTION guard_provider_account_identity_lifecycle()"
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE content_activity_refresh_requests, content_activity_projections, "
        "content_activity_observations, provider_account_identity_verifications, "
        "provider_account_identities IN ACCESS EXCLUSIVE MODE"
    )
    _preflight_downgrade()

    op.execute(
        "DROP TRIGGER trg_provider_account_identities_lifecycle_guard "
        "ON provider_account_identities"
    )
    op.execute("DROP FUNCTION guard_provider_account_identity_lifecycle()")
    op.execute(
        "DROP TRIGGER trg_content_activity_projections_integrity_guard "
        "ON content_activity_projections"
    )
    op.execute("DROP FUNCTION guard_content_activity_projection_integrity()")
    op.execute(
        "DROP TRIGGER trg_content_activity_observations_trusted_identity_guard "
        "ON content_activity_observations"
    )
    op.execute("DROP FUNCTION guard_content_activity_observation_trusted_identity()")
    op.execute(
        "DROP TRIGGER trg_content_activity_observations_append_only "
        "ON content_activity_observations"
    )
    op.execute("DROP FUNCTION prevent_content_activity_observation_mutation()")
    op.execute(
        "DROP TRIGGER trg_provider_account_identity_verifications_append_only "
        "ON provider_account_identity_verifications"
    )
    op.execute("DROP FUNCTION prevent_provider_account_identity_verification_mutation()")

    op.drop_table("content_activity_refresh_requests")
    op.drop_table("content_activity_projections")
    op.drop_table("content_activity_observations")
    op.drop_table("provider_account_identity_verifications")
    op.drop_table("provider_account_identities")

    bind = op.get_bind()
    for enum_type in (
        content_activity_refresh_request_state,
        content_activity_scan_terminal_reason,
        content_activity_provider_error_class,
        content_activity_publication_type,
        content_activity_result,
        content_activity_coverage_status,
        content_activity_observation_status,
        content_activity_semantics,
        content_activity_provider,
        provider_account_identity_verification_outcome,
        provider_account_identity_verification_state,
        provider_account_identity_namespace,
    ):
        enum_type.drop(bind, checkfirst=True)

    # Audit enum labels are retained: PostgreSQL cannot safely remove shared enum values.
