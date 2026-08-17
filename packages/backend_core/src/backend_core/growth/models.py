"""Candidate Pool and Campaign persistence without domain services."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend_core.db.base import Base
from backend_core.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from backend_core.db.types import JSON_DOCUMENT
from backend_core.growth.enums import (
    CampaignReviewMode,
    CampaignStatus,
    CandidatePoolKind,
    CandidatePoolRunStatus,
    CandidatePoolStatus,
    CandidateResult,
    DuplicateHistoryPolicy,
    Phase3AOperationScope,
)


def enum_values(enum_type: type[Any]) -> list[str]:
    return [item.value for item in enum_type]


PHASE3A_BULK_ADD_RESULT_PAYLOAD_KEYS = (
    "campaign_id",
    "source_pool_run_id",
    "requested_count",
    "added_count",
    "restored_count",
    "already_active_count",
    "active_count_after",
)
_PHASE3A_BULK_ADD_RESULT_PAYLOAD_KEYS_SQL = ", ".join(
    f"'{key}'" for key in PHASE3A_BULK_ADD_RESULT_PAYLOAD_KEYS
)
_PHASE3A_UUID_PATTERN = (
    "^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-" "[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


def _phase3a_bulk_add_result_payload_constraint() -> str:
    """Return the fixed, redacted bulk-add replay shape for schema version 1."""

    return (
        "operation_scope <> 'CAMPAIGN_MEMBER_BULK_ADD'::phase3a_operation_scope OR "
        "result_schema_version <> 1 OR "
        "(result_payload ?& ARRAY["
        f"{_PHASE3A_BULK_ADD_RESULT_PAYLOAD_KEYS_SQL}]::text[] AND (result_payload - ARRAY["
        f"{_PHASE3A_BULK_ADD_RESULT_PAYLOAD_KEYS_SQL}]::text[]) = '{{}}'::jsonb AND "
        "jsonb_typeof(result_payload -> 'campaign_id') = 'string' AND "
        f"(result_payload ->> 'campaign_id') ~ '{_PHASE3A_UUID_PATTERN}' AND "
        "lower(result_payload ->> 'campaign_id') = result_entity_id::text AND "
        "(jsonb_typeof(result_payload -> 'source_pool_run_id') = 'null' OR "
        "(jsonb_typeof(result_payload -> 'source_pool_run_id') = 'string' AND "
        f"(result_payload ->> 'source_pool_run_id') ~ '{_PHASE3A_UUID_PATTERN}')) AND "
        "jsonb_typeof(result_payload -> 'requested_count') = 'number' AND "
        "(result_payload ->> 'requested_count') ~ '^[0-9]+$' AND "
        "jsonb_typeof(result_payload -> 'added_count') = 'number' AND "
        "(result_payload ->> 'added_count') ~ '^[0-9]+$' AND "
        "jsonb_typeof(result_payload -> 'restored_count') = 'number' AND "
        "(result_payload ->> 'restored_count') ~ '^[0-9]+$' AND "
        "jsonb_typeof(result_payload -> 'already_active_count') = 'number' AND "
        "(result_payload ->> 'already_active_count') ~ '^[0-9]+$' AND "
        "jsonb_typeof(result_payload -> 'active_count_after') = 'number' AND "
        "(result_payload ->> 'active_count_after') ~ '^[0-9]+$' AND "
        "((result_payload ->> 'added_count')::numeric + "
        "(result_payload ->> 'restored_count')::numeric + "
        "(result_payload ->> 'already_active_count')::numeric = "
        "(result_payload ->> 'requested_count')::numeric))"
    )


class Phase3AIdempotencyRecord(UUIDPrimaryKeyMixin, Base):
    """Committed Phase 3A mutation results available for durable replay."""

    __tablename__ = "phase3a_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "department_id",
            "operation_scope",
            "idempotency_key",
            name="uq_phase3a_idempotency_record_department_scope_key",
        ),
        ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_phase3a_idempotency_record_department",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 255",
            name="ck_phase3a_idempotency_record_key_length",
        ),
        CheckConstraint(
            "result_schema_version >= 1",
            name="ck_phase3a_idempotency_record_result_schema_version",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_phase3a_idempotency_record_request_hash",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "jsonb_typeof(result_payload) = 'object'",
            name="ck_phase3a_idempotency_record_payload_object",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "octet_length(result_payload::text) <= 16384",
            name="ck_phase3a_idempotency_record_payload_size",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            _phase3a_bulk_add_result_payload_constraint(),
            name="ck_phase3a_idempotency_record_bulk_add_payload",
        ).ddl_if(dialect="postgresql"),
    )

    department_id: Mapped[UUID] = mapped_column(nullable=False)
    operation_scope: Mapped[Phase3AOperationScope] = mapped_column(
        Enum(
            Phase3AOperationScope,
            name="phase3a_operation_scope",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_entity_id: Mapped[UUID] = mapped_column(nullable=False)
    result_schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CandidatePool(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "candidate_pools"
    __table_args__ = (
        UniqueConstraint("id", "department_id", name="uq_candidate_pool_department_pair"),
        ForeignKeyConstraint(
            ["owner_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_candidate_pool_owner_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_collection_job_id", "department_id"],
            ["collection_jobs.id", "collection_jobs.department_id"],
            name="fk_candidate_pool_source_collection_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_policy_id", "id"],
            ["targeting_policies.id", "targeting_policies.pool_id"],
            name="fk_candidate_pool_current_policy",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_candidate_pool_version"),
        Index(
            "ix_candidate_pools_department_status_created",
            "department_id",
            "status",
            "created_at",
            "id",
        ),
    )

    department_id: Mapped[UUID] = mapped_column(nullable=False)
    owner_operator_id: Mapped[UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[CandidatePoolKind] = mapped_column(
        Enum(CandidatePoolKind, name="candidate_pool_kind", values_callable=enum_values),
        nullable=False,
    )
    source_collection_job_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[CandidatePoolStatus] = mapped_column(
        Enum(CandidatePoolStatus, name="candidate_pool_status", values_callable=enum_values),
        nullable=False,
        default=CandidatePoolStatus.ACTIVE,
    )
    current_policy_id: Mapped[UUID | None] = mapped_column(nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class TargetingPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "targeting_policies"
    __table_args__ = (
        UniqueConstraint("pool_id", "version", name="uq_targeting_policy_pool_version"),
        UniqueConstraint("id", "pool_id", name="uq_targeting_policy_id_pool"),
        ForeignKeyConstraint(
            ["pool_id"],
            ["candidate_pools.id"],
            name="fk_targeting_policy_pool",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by_operator_id"],
            ["operators.id"],
            name="fk_targeting_policy_creator",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_targeting_policy_version"),
        CheckConstraint("schema_version >= 1", name="ck_targeting_policy_schema_version"),
    )

    pool_id: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_operator_id: Mapped[UUID] = mapped_column(nullable=False)


class CandidatePoolRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "candidate_pool_runs"
    __table_args__ = (
        UniqueConstraint(
            "pool_id", "idempotency_key", name="uq_candidate_pool_run_pool_idempotency"
        ),
        UniqueConstraint("id", "pool_id", name="uq_candidate_pool_run_id_pool"),
        ForeignKeyConstraint(
            ["pool_id"],
            ["candidate_pools.id"],
            name="fk_candidate_pool_run_pool",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["policy_id", "pool_id"],
            ["targeting_policies.id", "targeting_policies.pool_id"],
            name="fk_candidate_pool_run_policy_pool",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "match_count >= 0 AND unknown_count >= 0 AND not_match_count >= 0",
            name="ck_candidate_pool_run_counts_nonnegative",
        ),
        Index("ix_candidate_pool_runs_pool_created", "pool_id", "created_at", "id"),
    )

    pool_id: Mapped[UUID] = mapped_column(nullable=False)
    policy_id: Mapped[UUID] = mapped_column(nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_watermark: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    status: Mapped[CandidatePoolRunStatus] = mapped_column(
        Enum(CandidatePoolRunStatus, name="candidate_pool_run_status", values_callable=enum_values),
        nullable=False,
        default=CandidatePoolRunStatus.PENDING,
    )
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unknown_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    not_match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class CandidatePoolMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "candidate_pool_members"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "platform_account_id", name="uq_candidate_pool_member_run_account"
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["candidate_pool_runs.id"],
            name="fk_candidate_pool_member_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["influencer_id"],
            ["influencers.id"],
            name="fk_candidate_pool_member_influencer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_candidate_pool_member_account_influencer",
            ondelete="RESTRICT",
        ),
        Index("ix_candidate_pool_members_run_result", "run_id", "result", "id"),
    )

    run_id: Mapped[UUID] = mapped_column(nullable=False)
    influencer_id: Mapped[UUID] = mapped_column(nullable=False)
    platform_account_id: Mapped[UUID] = mapped_column(nullable=False)
    result: Mapped[CandidateResult] = mapped_column(
        Enum(CandidateResult, name="candidate_result", values_callable=enum_values), nullable=False
    )
    reason_codes: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    redacted_evidence: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class Campaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        UniqueConstraint("id", "department_id", name="uq_campaign_department_pair"),
        ForeignKeyConstraint(
            ["owner_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_campaign_owner_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_campaign_creator_department",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_campaign_version"),
        CheckConstraint(
            "review_count IS NULL OR review_count > 0", name="ck_campaign_review_count"
        ),
        CheckConstraint(
            "(review_mode = 'FIRST_N' AND review_count IS NOT NULL) OR "
            "(review_mode != 'FIRST_N' AND review_count IS NULL)",
            name="ck_campaign_review_config",
        ),
        CheckConstraint(
            "duplicate_window_days IS NULL OR duplicate_window_days > 0",
            name="ck_campaign_duplicate_window",
        ),
        CheckConstraint(
            "(duplicate_history_policy = 'BLOCK_WITHIN_WINDOW' "
            "AND duplicate_window_days IS NOT NULL) OR "
            "(duplicate_history_policy != 'BLOCK_WITHIN_WINDOW')",
            name="ck_campaign_duplicate_policy_window",
        ),
        Index(
            "ix_campaigns_department_status_updated", "department_id", "status", "updated_at", "id"
        ),
    )

    department_id: Mapped[UUID] = mapped_column(nullable=False)
    owner_operator_id: Mapped[UUID] = mapped_column(nullable=False)
    created_by_operator_id: Mapped[UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, name="campaign_status", values_callable=enum_values),
        nullable=False,
        default=CampaignStatus.DRAFT,
    )
    review_mode: Mapped[CampaignReviewMode] = mapped_column(
        Enum(CampaignReviewMode, name="campaign_review_mode", values_callable=enum_values),
        nullable=False,
        default=CampaignReviewMode.FIRST_N,
    )
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True, default=50)
    duplicate_history_policy: Mapped[DuplicateHistoryPolicy] = mapped_column(
        Enum(DuplicateHistoryPolicy, name="duplicate_history_policy", values_callable=enum_values),
        nullable=False,
        default=DuplicateHistoryPolicy.ALLOW_WITH_WARNING,
    )
    duplicate_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CampaignMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "campaign_members"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "influencer_id", name="uq_campaign_member_campaign_influencer"
        ),
        UniqueConstraint(
            "id", "campaign_id", "influencer_id", name="uq_campaign_member_id_campaign_influencer"
        ),
        ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_campaign_member_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["campaign_id", "department_id"],
            ["campaigns.id", "campaigns.department_id"],
            name="fk_campaign_member_campaign_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["influencer_id"],
            ["influencers.id"],
            name="fk_campaign_member_influencer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["preferred_platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_campaign_member_preferred_account_influencer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_pool_run_id"],
            ["candidate_pool_runs.id"],
            name="fk_campaign_member_source_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["added_by_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_campaign_member_adder_department",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_campaign_member_version"),
        Index("ix_campaign_members_campaign_removed", "campaign_id", "removed_at", "id"),
    )

    department_id: Mapped[UUID] = mapped_column(nullable=False)
    campaign_id: Mapped[UUID] = mapped_column(nullable=False)
    influencer_id: Mapped[UUID] = mapped_column(nullable=False)
    preferred_platform_account_id: Mapped[UUID] = mapped_column(nullable=False)
    source_pool_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    added_by_operator_id: Mapped[UUID] = mapped_column(nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
