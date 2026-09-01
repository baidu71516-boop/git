"""Add the minimal Huitun Douyin runtime-capture evidence contract.

Revision ID: 0009_douyin_runtime_capture_v1
Revises: 0008_content_activity_p0
Create Date: 2026-08-28

The migration deliberately extends the existing Content Activity ledger rather
than creating a parallel Douyin table.  The browser bridge stores only a
one-time capability digest; it never stores Huitun cookies, Authorization, raw
encrypted envelopes, or a decrypted provider response body.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_douyin_runtime_capture_v1"
down_revision: str | None = "0008_content_activity_p0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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

HUITUN_IDENTITY_SOURCE = "HUITUN_DOUYIN_AWEME_LIST_RUNTIME_CAPTURE"
HUITUN_IDENTITY_CONTRACT_VERSION = "HUITUN_DOUYIN_AWEME_LIST_RUNTIME_SEMANTIC_V1"
HUITUN_PROVIDER = "HUITUN_DOUYIN_AWEME_LIST"
HUITUN_PRODUCT = "HUITUN_DOUYIN_AWEME_LIST"
HUITUN_ENDPOINT = "/user/awemeList"
HUITUN_ENDPOINT_VERSION = "RUNTIME_SEMANTIC_V1"
HUITUN_ADAPTER_VERSION = "HUITUN_DOUYIN_RUNTIME_CAPTURE_ADAPTER_V1"
HUITUN_CAPABILITY_POLICY_VERSION = "HUITUN_DOUYIN_RUNTIME_CAPTURE_POLICY_V1"
HUITUN_RESPONSE_SCHEMA_VERSION = "HUITUN_DOUYIN_AWEME_LIST_SEMANTIC_SCHEMA_V1"
HUITUN_VISIBILITY_POLICY_VERSION = "HUITUN_DOUYIN_RETURNED_SCOPE_POLICY_V1"
HUITUN_TIMESTAMP_ENCODING = "HUITUN_PUBLISH_TIME_ASIA_SHANGHAI"
HUITUN_SOURCE_TIMEZONE = "Asia/Shanghai"


def _install_observation_identity_guard(*, include_huitun: bool) -> None:
    """Install the immutable COMPLETE-observation allow-list.

    ``CURRENT_PUBLIC_VISIBLE`` stays exactly scoped to the old XHS contract.
    The new Huitun branch explicitly permits only returned-scope exact or
    bounded evidence; it can never populate a trusted-current projection.
    """

    huitun_branch = ""
    if include_huitun:
        huitun_branch = f"""
            ELSIF NEW.platform = 'douyin' THEN
                IF NEW.schema_version <> 1
                    OR NEW.activity_semantics <> 'HUITUN_RETURNED_SCOPE'
                    OR NEW.activity_source_provider <> '{HUITUN_PROVIDER}'
                    OR NEW.provider_product <> '{HUITUN_PRODUCT}'
                    OR NEW.endpoint <> '{HUITUN_ENDPOINT}'
                    OR NEW.endpoint_version <> '{HUITUN_ENDPOINT_VERSION}'
                    OR NEW.adapter_version <> '{HUITUN_ADAPTER_VERSION}'
                    OR NEW.capability_policy_version <> '{HUITUN_CAPABILITY_POLICY_VERSION}'
                    OR NEW.response_schema_version <> '{HUITUN_RESPONSE_SCHEMA_VERSION}'
                    OR NEW.visibility_policy_version <> '{HUITUN_VISIBILITY_POLICY_VERSION}'
                    OR NEW.timestamp_encoding <> '{HUITUN_TIMESTAMP_ENCODING}'
                    OR NEW.source_timezone <> '{HUITUN_SOURCE_TIMEZONE}'
                    OR NEW.timezone_basis <> '{HUITUN_TIMESTAMP_ENCODING}'
                    OR NEW.coverage_end_at IS NULL
                    OR NEW.coverage_end_at > NEW.observed_at
                    OR NEW.coverage_end_at < NEW.observed_at - INTERVAL '5 minutes'
                    OR NEW.scan_terminal_reason <> 'SINGLE_RESPONSE_COMPLETE'
                    OR NEW.scanned_page_count <> 1
                    OR NEW.provider_error_class IS NOT NULL
                    OR NEW.provider_error_code IS NOT NULL
                    OR NOT (
                        (
                            NEW.coverage_status = 'LATEST_BOUND_PROVEN'
                            AND NEW.activity_result = 'PUBLICATION_FOUND'
                            AND NEW.scanned_item_count >= 1
                            AND NEW.latest_publication_type = 'OTHER'
                        )
                        OR (
                            NEW.coverage_status = 'LOOKBACK_BOUNDED'
                            AND NEW.activity_result = 'AT_LEAST_LOOKBACK_INACTIVE'
                            AND NEW.coverage_start_at IS NOT NULL
                            AND NEW.coverage_start_at < NEW.coverage_end_at
                            AND NEW.scanned_item_count = 0
                        )
                    ) THEN
                    RAISE EXCEPTION 'COMPLETE observation violates Huitun returned-scope contract';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM provider_account_identities AS identity_binding
                    JOIN provider_account_identity_verifications AS verification_event
                        ON verification_event.provider_account_identity_id = identity_binding.id
                    WHERE identity_binding.id = NEW.provider_account_identity_id
                        AND identity_binding.platform_account_id = NEW.platform_account_id
                        AND identity_binding.platform = NEW.platform
                        AND identity_binding.namespace = 'douyin.huitun_uid'
                        AND identity_binding.verification_state = 'VERIFIED_CURRENT'
                        AND identity_binding.identity_source = '{HUITUN_IDENTITY_SOURCE}'
                        AND identity_binding.resolver_contract_version =
                            '{HUITUN_IDENTITY_CONTRACT_VERSION}'
                        AND identity_binding.verified_at <= NEW.observed_at
                        AND verification_event.id = NEW.provider_account_identity_verification_id
                        AND verification_event.identity_source = '{HUITUN_IDENTITY_SOURCE}'
                        AND verification_event.resolver_contract_version =
                            '{HUITUN_IDENTITY_CONTRACT_VERSION}'
                        AND verification_event.verification_outcome = 'POLICY_VERIFIED'
                        AND verification_event.verified_at <= NEW.observed_at
                ) THEN
                    RAISE EXCEPTION
                        'COMPLETE Huitun observation requires current verified request uid';
                END IF;
        """

    op.execute(
        "DROP TRIGGER IF EXISTS trg_content_activity_observations_trusted_identity_guard "
        "ON content_activity_observations"
    )
    op.execute("DROP FUNCTION IF EXISTS guard_content_activity_observation_trusted_identity()")
    op.execute(
        f"""
        CREATE FUNCTION guard_content_activity_observation_trusted_identity()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.observation_status <> 'COMPLETE' THEN
                RETURN NEW;
            END IF;

            IF NEW.platform = 'xiaohongshu' THEN
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
            {huitun_branch}
            ELSE
                RAISE EXCEPTION 'COMPLETE observation platform is not allowlisted';
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


def _preflight_downgrade() -> None:
    has_douyin_runtime_evidence = bool(
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM provider_account_identities
                    WHERE platform = 'douyin'
                        OR namespace = 'douyin.huitun_uid'
                ) OR EXISTS (
                    SELECT 1
                    FROM content_activity_observations
                    WHERE platform = 'douyin'
                        OR activity_semantics = 'HUITUN_RETURNED_SCOPE'
                        OR activity_source_provider = 'HUITUN_DOUYIN_AWEME_LIST'
                ) OR EXISTS (
                    SELECT 1
                    FROM content_activity_refresh_requests
                    WHERE capture_token_digest IS NOT NULL
                )
                """
            )
        )
        .scalar_one()
    )
    if has_douyin_runtime_evidence:
        raise RuntimeError(
            "0009 downgrade blocked: Huitun Douyin runtime capture identity, observation, "
            "or capability records cannot be destroyed"
        )


def upgrade() -> None:
    # PostgreSQL enum values are shared schema contract, so their additions are
    # committed before the later DDL references them.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE platform_enum ADD VALUE IF NOT EXISTS 'douyin'")
        op.execute(
            "ALTER TYPE provider_account_identity_namespace "
            "ADD VALUE IF NOT EXISTS 'douyin.huitun_uid'"
        )
        op.execute(
            "ALTER TYPE content_activity_provider "
            "ADD VALUE IF NOT EXISTS 'HUITUN_DOUYIN_AWEME_LIST'"
        )
        op.execute(
            "ALTER TYPE content_activity_semantics "
            "ADD VALUE IF NOT EXISTS 'HUITUN_RETURNED_SCOPE'"
        )

    op.add_column(
        "content_activity_refresh_requests",
        sa.Column("capture_token_digest", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_content_activity_refresh_request_capture_token_digest",
        "content_activity_refresh_requests",
        "capture_token_digest IS NULL OR length(capture_token_digest) = 64",
    )
    op.drop_constraint(
        "ck_provider_account_identity_platform_namespace",
        "provider_account_identities",
        type_="check",
    )
    op.create_check_constraint(
        "ck_provider_account_identity_platform_namespace",
        "provider_account_identities",
        "(platform = 'xiaohongshu' AND namespace = 'xiaohongshu.userid') OR "
        "(platform = 'douyin' AND namespace = 'douyin.huitun_uid')",
    )
    _install_observation_identity_guard(include_huitun=True)


def downgrade() -> None:
    op.execute(
        "LOCK TABLE content_activity_refresh_requests, content_activity_observations, "
        "provider_account_identity_verifications, provider_account_identities "
        "IN ACCESS EXCLUSIVE MODE"
    )
    _preflight_downgrade()
    _install_observation_identity_guard(include_huitun=False)
    op.drop_constraint(
        "ck_content_activity_refresh_request_capture_token_digest",
        "content_activity_refresh_requests",
        type_="check",
    )
    op.drop_column("content_activity_refresh_requests", "capture_token_digest")
    op.drop_constraint(
        "ck_provider_account_identity_platform_namespace",
        "provider_account_identities",
        type_="check",
    )
    op.create_check_constraint(
        "ck_provider_account_identity_platform_namespace",
        "provider_account_identities",
        "platform = 'xiaohongshu' AND namespace = 'xiaohongshu.userid'",
    )
    # PostgreSQL cannot safely remove enum labels.  The 0008 schema remains
    # valid because its former enum values and constraints still govern rows.
