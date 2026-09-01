"""Platform-neutral, immutable Content Activity persistence models."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityProvider,
    ContentActivityProviderErrorClass,
    ContentActivityPublicationType,
    ContentActivityRefreshRequestState,
    ContentActivityResult,
    ContentActivityScanTerminalReason,
    ContentActivitySemantics,
    ProviderAccountIdentityNamespace,
    ProviderAccountIdentityVerificationOutcome,
    ProviderAccountIdentityVerificationState,
)
from backend_core.db.base import Base
from backend_core.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from backend_core.influencers.enums import Platform


def enum_values(enum_type: type[Any]) -> list[str]:
    return [item.value for item in enum_type]


class ProviderAccountIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One append/supersede canonical external-identity binding episode."""

    __tablename__ = "provider_account_identities"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "platform_account_id",
            "platform",
            name="uq_provider_account_identity_id_account_platform",
        ),
        UniqueConstraint(
            "platform",
            "namespace",
            "opaque_external_identity",
            name="uq_provider_account_identity_external_owner",
        ),
        ForeignKeyConstraint(
            ["platform_account_id", "platform"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.platform"],
            name="fk_provider_account_identity_account_platform",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(trim(opaque_external_identity)) BETWEEN 1 AND 512",
            name="ck_provider_account_identity_opaque_nonblank",
        ),
        CheckConstraint(
            "length(trim(identity_source)) BETWEEN 1 AND 120",
            name="ck_provider_account_identity_source_nonblank",
        ),
        CheckConstraint(
            "length(trim(resolver_contract_version)) BETWEEN 1 AND 160",
            name="ck_provider_account_identity_resolver_version_nonblank",
        ),
        CheckConstraint(
            "length(trim(provenance_ref)) BETWEEN 1 AND 160",
            name="ck_provider_account_identity_provenance_nonblank",
        ),
        CheckConstraint("lock_version >= 1", name="ck_provider_account_identity_lock_version"),
        CheckConstraint(
            "(platform = 'xiaohongshu' AND namespace = 'xiaohongshu.userid') OR "
            "(platform = 'douyin' AND namespace = 'douyin.huitun_uid')",
            name="ck_provider_account_identity_platform_namespace",
        ),
        CheckConstraint(
            "resolved_at <= verified_at",
            name="ck_provider_account_identity_resolution_before_verification",
        ),
        CheckConstraint(
            "(verification_state = 'VERIFIED_CURRENT' AND superseded_at IS NULL "
            "AND revoked_at IS NULL) OR "
            "(verification_state = 'SUPERSEDED' AND superseded_at IS NOT NULL "
            "AND revoked_at IS NULL AND superseded_at >= verified_at) OR "
            "(verification_state = 'REVOKED' AND revoked_at IS NOT NULL "
            "AND superseded_at IS NULL AND revoked_at >= verified_at)",
            name="ck_provider_account_identity_lifecycle",
        ),
        Index(
            "uq_provider_account_identity_current_account_namespace",
            "platform_account_id",
            "namespace",
            unique=True,
            postgresql_where=text("verification_state = 'VERIFIED_CURRENT'"),
            sqlite_where=text("verification_state = 'VERIFIED_CURRENT'"),
        ),
        Index(
            "ix_provider_account_identity_account_namespace_state",
            "platform_account_id",
            "namespace",
            "verification_state",
        ),
    )

    platform_account_id: Mapped[UUID] = mapped_column(nullable=False)
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, name="platform_enum", values_callable=enum_values), nullable=False
    )
    namespace: Mapped[ProviderAccountIdentityNamespace] = mapped_column(
        Enum(
            ProviderAccountIdentityNamespace,
            name="provider_account_identity_namespace",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    opaque_external_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    identity_source: Mapped[str] = mapped_column(String(120), nullable=False)
    resolver_contract_version: Mapped[str] = mapped_column(String(160), nullable=False)
    verification_state: Mapped[ProviderAccountIdentityVerificationState] = mapped_column(
        Enum(
            ProviderAccountIdentityVerificationState,
            name="provider_account_identity_verification_state",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provenance_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProviderAccountIdentityVerification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable accepted verification evidence for one identity binding."""

    __tablename__ = "provider_account_identity_verifications"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "provider_account_identity_id",
            name="uq_provider_account_identity_verification_id_binding",
        ),
        UniqueConstraint(
            "identity_source",
            "resolver_contract_version",
            "idempotency_key",
            name="uq_provider_account_identity_verification_idempotency",
        ),
        ForeignKeyConstraint(
            ["provider_account_identity_id"],
            ["provider_account_identities.id"],
            name="fk_provider_account_identity_verification_binding",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(trim(identity_source)) BETWEEN 1 AND 120",
            name="ck_provider_identity_verification_source_nonblank",
        ),
        CheckConstraint(
            "length(trim(resolver_contract_version)) BETWEEN 1 AND 160",
            name="ck_provider_identity_verification_resolver_version_nonblank",
        ),
        CheckConstraint(
            "length(trim(provenance_ref)) BETWEEN 1 AND 160",
            name="ck_provider_identity_verification_provenance_nonblank",
        ),
        CheckConstraint(
            "length(trim(idempotency_key)) BETWEEN 1 AND 255",
            name="ck_provider_identity_verification_idempotency_nonblank",
        ),
        Index(
            "ix_provider_account_identity_verification_history",
            "provider_account_identity_id",
            "verified_at",
            "id",
        ),
    )

    provider_account_identity_id: Mapped[UUID] = mapped_column(nullable=False)
    identity_source: Mapped[str] = mapped_column(String(120), nullable=False)
    resolver_contract_version: Mapped[str] = mapped_column(String(160), nullable=False)
    verification_outcome: Mapped[ProviderAccountIdentityVerificationOutcome] = mapped_column(
        Enum(
            ProviderAccountIdentityVerificationOutcome,
            name="provider_account_identity_verification_outcome",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provenance_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)


class ContentActivityObservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable normalized result of one Content Activity refresh attempt."""

    __tablename__ = "content_activity_observations"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "platform_account_id",
            "platform",
            name="uq_content_activity_observation_id_account_platform",
        ),
        ForeignKeyConstraint(
            ["platform_account_id", "platform"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.platform"],
            name="fk_content_activity_observation_account_platform",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["provider_account_identity_id", "platform_account_id", "platform"],
            [
                "provider_account_identities.id",
                "provider_account_identities.platform_account_id",
                "provider_account_identities.platform",
            ],
            name="fk_content_activity_observation_identity_account_platform",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["provider_account_identity_verification_id", "provider_account_identity_id"],
            [
                "provider_account_identity_verifications.id",
                "provider_account_identity_verifications.provider_account_identity_id",
            ],
            name="fk_content_activity_observation_identity_verification",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "schema_version >= 1", name="ck_content_activity_observation_schema_version"
        ),
        CheckConstraint(
            "(provider_account_identity_id IS NULL "
            "AND provider_account_identity_verification_id IS NULL) OR "
            "(provider_account_identity_id IS NOT NULL "
            "AND provider_account_identity_verification_id IS NOT NULL)",
            name="ck_content_activity_observation_identity_pair",
        ),
        CheckConstraint(
            "observation_status <> 'COMPLETE' "
            "OR (provider_account_identity_id IS NOT NULL "
            "AND provider_account_identity_verification_id IS NOT NULL)",
            name="ck_content_activity_observation_trusted_identity",
        ),
        CheckConstraint(
            "attempt_started_at <= observed_at",
            name="ck_content_activity_observation_attempt_range",
        ),
        CheckConstraint(
            "coverage_start_at IS NULL OR coverage_end_at IS NULL "
            "OR coverage_start_at <= coverage_end_at",
            name="ck_content_activity_observation_coverage_range",
        ),
        CheckConstraint(
            "last_publication_at IS NULL OR last_publication_at <= observed_at",
            name="ck_content_activity_observation_publication_not_future",
        ),
        CheckConstraint(
            "scanned_page_count >= 0 AND scanned_item_count >= 0",
            name="ck_content_activity_observation_scan_counts",
        ),
        CheckConstraint(
            "co_latest_publication_count IS NULL OR co_latest_publication_count >= 1",
            name="ck_content_activity_observation_co_latest_count",
        ),
        CheckConstraint(
            "normalized_timezone = 'UTC'",
            name="ck_content_activity_observation_normalized_timezone",
        ),
        CheckConstraint(
            "length(trim(provider_product)) BETWEEN 1 AND 120 AND "
            "length(trim(endpoint)) BETWEEN 1 AND 255 AND "
            "length(trim(endpoint_version)) BETWEEN 1 AND 120 AND "
            "length(trim(adapter_version)) BETWEEN 1 AND 160 AND "
            "length(trim(capability_policy_version)) BETWEEN 1 AND 160 AND "
            "length(trim(response_schema_version)) BETWEEN 1 AND 160 AND "
            "length(trim(visibility_policy_version)) BETWEEN 1 AND 160",
            name="ck_content_activity_observation_contract_versions",
        ),
        CheckConstraint(
            "(request_ref IS NULL OR length(trim(request_ref)) BETWEEN 1 AND 160) AND "
            "(provenance_ref IS NULL OR length(trim(provenance_ref)) BETWEEN 1 AND 160) AND "
            "(provider_error_code IS NULL OR length(trim(provider_error_code)) BETWEEN 1 AND 80)",
            name="ck_content_activity_observation_sanitized_refs",
        ),
        CheckConstraint(
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
        CheckConstraint(
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
        Index(
            "ix_content_activity_observation_account_history",
            "platform_account_id",
            "observed_at",
            "id",
        ),
    )

    platform_account_id: Mapped[UUID] = mapped_column(nullable=False)
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, name="platform_enum", values_callable=enum_values), nullable=False
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    activity_semantics: Mapped[ContentActivitySemantics] = mapped_column(
        Enum(
            ContentActivitySemantics,
            name="content_activity_semantics",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    provider_account_identity_id: Mapped[UUID | None] = mapped_column(nullable=True)
    provider_account_identity_verification_id: Mapped[UUID | None] = mapped_column(nullable=True)
    activity_source_provider: Mapped[ContentActivityProvider] = mapped_column(
        Enum(
            ContentActivityProvider,
            name="content_activity_provider",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    provider_product: Mapped[str] = mapped_column(String(120), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint_version: Mapped[str] = mapped_column(String(120), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(160), nullable=False)
    capability_policy_version: Mapped[str] = mapped_column(String(160), nullable=False)
    response_schema_version: Mapped[str] = mapped_column(String(160), nullable=False)
    visibility_policy_version: Mapped[str] = mapped_column(String(160), nullable=False)
    attempt_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_status: Mapped[ContentActivityObservationStatus] = mapped_column(
        Enum(
            ContentActivityObservationStatus,
            name="content_activity_observation_status",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    coverage_status: Mapped[ContentActivityCoverageStatus] = mapped_column(
        Enum(
            ContentActivityCoverageStatus,
            name="content_activity_coverage_status",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    activity_result: Mapped[ContentActivityResult] = mapped_column(
        Enum(ContentActivityResult, name="content_activity_result", values_callable=enum_values),
        nullable=False,
    )
    last_publication_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latest_publication_id_namespace: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latest_publication_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    latest_publication_type: Mapped[ContentActivityPublicationType | None] = mapped_column(
        Enum(
            ContentActivityPublicationType,
            name="content_activity_publication_type",
            values_callable=enum_values,
        ),
        nullable=True,
    )
    co_latest_publication_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coverage_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    coverage_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timestamp_encoding: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timezone_basis: Mapped[str | None] = mapped_column(String(80), nullable=True)
    normalized_timezone: Mapped[str] = mapped_column(String(32), nullable=False, default="UTC")
    request_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provenance_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    scan_terminal_reason: Mapped[ContentActivityScanTerminalReason] = mapped_column(
        Enum(
            ContentActivityScanTerminalReason,
            name="content_activity_scan_terminal_reason",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    scanned_page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scanned_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_error_class: Mapped[ContentActivityProviderErrorClass | None] = mapped_column(
        Enum(
            ContentActivityProviderErrorClass,
            name="content_activity_provider_error_class",
            values_callable=enum_values,
        ),
        nullable=True,
    )
    provider_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ContentActivityProjection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Current trusted and latest-attempt projections for one platform account."""

    __tablename__ = "content_activity_projections"
    __table_args__ = (
        UniqueConstraint("platform_account_id", name="uq_content_activity_projection_account"),
        ForeignKeyConstraint(
            ["platform_account_id", "platform"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.platform"],
            name="fk_content_activity_projection_account_platform",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["latest_attempt_observation_id", "platform_account_id", "platform"],
            [
                "content_activity_observations.id",
                "content_activity_observations.platform_account_id",
                "content_activity_observations.platform",
            ],
            name="fk_content_activity_projection_latest_attempt",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["trusted_observation_id", "platform_account_id", "platform"],
            [
                "content_activity_observations.id",
                "content_activity_observations.platform_account_id",
                "content_activity_observations.platform",
            ],
            name="fk_content_activity_projection_trusted_observation",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_content_activity_projection_version"),
        CheckConstraint(
            "(latest_attempt_provider_error_code IS NULL OR "
            "length(trim(latest_attempt_provider_error_code)) BETWEEN 1 AND 80)",
            name="ck_content_activity_projection_latest_attempt_error",
        ),
        CheckConstraint(
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
        CheckConstraint(
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
        CheckConstraint(
            "co_latest_publication_count IS NULL OR co_latest_publication_count >= 1",
            name="ck_content_activity_projection_co_latest_count",
        ),
        Index(
            "ix_content_activity_projection_trusted_publication",
            "platform",
            "last_publication_at",
            "trusted_observed_at",
            "platform_account_id",
            postgresql_where=text(
                "trusted_observation_id IS NOT NULL "
                "AND trusted_activity_result = 'PUBLICATION_FOUND'"
            ),
            sqlite_where=text(
                "trusted_observation_id IS NOT NULL "
                "AND trusted_activity_result = 'PUBLICATION_FOUND'"
            ),
        ),
        Index(
            "ix_content_activity_projection_latest_attempt",
            "latest_attempt_observed_at",
            "platform_account_id",
        ),
    )

    platform_account_id: Mapped[UUID] = mapped_column(nullable=False)
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, name="platform_enum", values_callable=enum_values), nullable=False
    )
    latest_attempt_observation_id: Mapped[UUID] = mapped_column(nullable=False)
    latest_attempt_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    latest_attempt_observation_status: Mapped[ContentActivityObservationStatus] = mapped_column(
        Enum(
            ContentActivityObservationStatus,
            name="content_activity_observation_status",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    latest_attempt_coverage_status: Mapped[ContentActivityCoverageStatus] = mapped_column(
        Enum(
            ContentActivityCoverageStatus,
            name="content_activity_coverage_status",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    latest_attempt_activity_result: Mapped[ContentActivityResult] = mapped_column(
        Enum(ContentActivityResult, name="content_activity_result", values_callable=enum_values),
        nullable=False,
    )
    latest_attempt_provider_error_class: Mapped[ContentActivityProviderErrorClass | None] = (
        mapped_column(
            Enum(
                ContentActivityProviderErrorClass,
                name="content_activity_provider_error_class",
                values_callable=enum_values,
            ),
            nullable=True,
        )
    )
    latest_attempt_provider_error_code: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    trusted_observation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    trusted_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trusted_observation_status: Mapped[ContentActivityObservationStatus | None] = mapped_column(
        Enum(
            ContentActivityObservationStatus,
            name="content_activity_observation_status",
            values_callable=enum_values,
        ),
        nullable=True,
    )
    trusted_coverage_status: Mapped[ContentActivityCoverageStatus | None] = mapped_column(
        Enum(
            ContentActivityCoverageStatus,
            name="content_activity_coverage_status",
            values_callable=enum_values,
        ),
        nullable=True,
    )
    trusted_activity_result: Mapped[ContentActivityResult | None] = mapped_column(
        Enum(ContentActivityResult, name="content_activity_result", values_callable=enum_values),
        nullable=True,
    )
    trusted_capability_policy_version: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    last_publication_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latest_publication_id_namespace: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latest_publication_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    latest_publication_type: Mapped[ContentActivityPublicationType | None] = mapped_column(
        Enum(
            ContentActivityPublicationType,
            name="content_activity_publication_type",
            values_callable=enum_values,
        ),
        nullable=True,
    )
    co_latest_publication_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ContentActivityRefreshRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bounded, durable per-account refresh request; not a generic RefreshQueue item."""

    __tablename__ = "content_activity_refresh_requests"
    __table_args__ = (
        UniqueConstraint("request_token", name="uq_content_activity_refresh_request_token"),
        UniqueConstraint(
            "platform_account_id",
            "idempotency_key",
            name="uq_content_activity_refresh_request_account_idempotency",
        ),
        ForeignKeyConstraint(
            ["platform_account_id", "platform"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.platform"],
            name="fk_content_activity_refresh_request_account_platform",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["last_observation_id", "platform_account_id", "platform"],
            [
                "content_activity_observations.id",
                "content_activity_observations.platform_account_id",
                "content_activity_observations.platform",
            ],
            name="fk_content_activity_refresh_request_last_observation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["requested_by_operator_id"],
            ["operators.id"],
            name="fk_content_activity_refresh_request_requester",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="ck_content_activity_refresh_request_attempt_bounds",
        ),
        CheckConstraint(
            "lease_generation >= 0", name="ck_content_activity_refresh_request_lease_generation"
        ),
        CheckConstraint(
            "length(trim(idempotency_key)) BETWEEN 1 AND 255",
            name="ck_content_activity_refresh_request_idempotency_nonblank",
        ),
        CheckConstraint(
            "(last_error_code IS NULL OR length(trim(last_error_code)) BETWEEN 1 AND 80)",
            name="ck_content_activity_refresh_request_error_code",
        ),
        CheckConstraint(
            "capture_token_digest IS NULL OR length(capture_token_digest) = 64",
            name="ck_content_activity_refresh_request_capture_token_digest",
        ),
        CheckConstraint(
            "(state IN ('PENDING', 'RETRY_WAIT') AND finished_at IS NULL "
            "AND lease_expires_at IS NULL AND next_attempt_at IS NOT NULL) OR "
            "(state = 'RUNNING' AND finished_at IS NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(state IN ('SUCCEEDED', 'FAILED', 'CANCELLED') "
            "AND finished_at IS NOT NULL AND lease_expires_at IS NULL)",
            name="ck_content_activity_refresh_request_lifecycle",
        ),
        CheckConstraint(
            "state <> 'SUCCEEDED' OR last_observation_id IS NOT NULL",
            name="ck_content_activity_refresh_request_success_observation",
        ),
        Index(
            "uq_content_activity_refresh_request_active_account",
            "platform_account_id",
            unique=True,
            postgresql_where=text("state IN ('PENDING', 'RUNNING', 'RETRY_WAIT')"),
            sqlite_where=text("state IN ('PENDING', 'RUNNING', 'RETRY_WAIT')"),
        ),
        Index(
            "ix_content_activity_refresh_request_due",
            "state",
            "next_attempt_at",
            "created_at",
            "id",
            postgresql_where=text("state IN ('PENDING', 'RETRY_WAIT')"),
            sqlite_where=text("state IN ('PENDING', 'RETRY_WAIT')"),
        ),
        Index(
            "ix_content_activity_refresh_request_expired_lease",
            "lease_expires_at",
            "id",
            postgresql_where=text("state = 'RUNNING'"),
            sqlite_where=text("state = 'RUNNING'"),
        ),
    )

    platform_account_id: Mapped[UUID] = mapped_column(nullable=False)
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, name="platform_enum", values_callable=enum_values), nullable=False
    )
    requested_by_operator_id: Mapped[UUID | None] = mapped_column(nullable=True)
    request_token: Mapped[UUID] = mapped_column(Uuid, nullable=False, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[ContentActivityRefreshRequestState] = mapped_column(
        Enum(
            ContentActivityRefreshRequestState,
            name="content_activity_refresh_request_state",
            values_callable=enum_values,
        ),
        nullable=False,
        default=ContentActivityRefreshRequestState.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_observation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Only a SHA-256 digest of an application-owned, single-use bridge token is
    # retained. Huitun cookies, Authorization values, and browser session data
    # are never written to this ledger.
    capture_token_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
