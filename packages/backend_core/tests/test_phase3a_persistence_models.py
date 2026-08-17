"""Phase 3A persistence metadata contract tests."""

import backend_core.db.models  # noqa: F401
from backend_core.audit.enums import AuditAction
from backend_core.db import Base
from backend_core.growth.enums import (
    CampaignStatus,
    CandidatePoolKind,
    CandidateResult,
    Phase3AOperationScope,
)
from backend_core.outreach.enums import (
    OutreachChannel,
    OutreachPriority,
    OutreachPrioritySource,
    OutreachTaskState,
)
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

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

PHASE3A_AUDIT_ACTIONS = [
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
]


def constraint_names(table_name: str, kind: type[object]) -> set[str]:
    table = Base.metadata.tables[table_name]
    return {constraint.name for constraint in table.constraints if isinstance(constraint, kind)}


def test_phase3a_tables_are_registered() -> None:
    assert PHASE3_TABLES <= set(Base.metadata.tables)


def test_phase3a_enums_are_closed_to_the_frozen_contract() -> None:
    assert [item.value for item in CandidatePoolKind] == ["POTENTIAL_SELLER", "POTENTIAL_BUYER"]
    assert [item.value for item in CandidateResult] == ["MATCH", "UNKNOWN"]
    assert [item.value for item in CampaignStatus] == ["DRAFT", "ACTIVE", "PAUSED", "CLOSED"]
    assert [item.value for item in OutreachChannel] == [
        "EMAIL",
        "XIAOHONGSHU_PRIVATE_MESSAGE",
        "DOUYIN_PRIVATE_MESSAGE",
        "WECHAT",
        "MANUAL",
    ]
    assert [item.value for item in OutreachTaskState] == [
        "REVIEW_REQUIRED",
        "READY",
        "SENT",
        "STOPPED",
        "FAILED",
    ]
    assert [item.value for item in OutreachPriority] == ["NORMAL", "HIGH"]
    assert [item.value for item in OutreachPrioritySource] == ["DEFAULT", "MANUAL", "POLICY"]
    assert [item.value for item in Phase3AOperationScope] == [
        "CANDIDATE_POOL_CREATE",
        "TARGETING_POLICY_CREATE",
        "CAMPAIGN_CREATE",
        "CAMPAIGN_MEMBER_BULK_ADD",
        "OUTREACH_TARGET_CREATE",
    ]
    assert [item.value for item in AuditAction][-len(PHASE3A_AUDIT_ACTIONS) :] == (
        PHASE3A_AUDIT_ACTIONS
    )


def test_campaign_member_and_target_ownership_constraints_are_present() -> None:
    member_fks = constraint_names("campaign_members", ForeignKeyConstraint)
    target_fks = constraint_names("outreach_targets", ForeignKeyConstraint)
    assert "fk_campaign_member_preferred_account_influencer" in member_fks
    assert "fk_outreach_target_contact_influencer" in target_fks
    assert "fk_outreach_target_account_influencer" in target_fks
    assert "uq_influencer_contact_id_influencer" in constraint_names(
        "influencer_contacts", UniqueConstraint
    )


def test_uniqueness_and_task_checks_are_present() -> None:
    assert "uq_candidate_pool_member_run_account" in constraint_names(
        "candidate_pool_members", UniqueConstraint
    )
    assert "uq_campaign_member_campaign_influencer" in constraint_names(
        "campaign_members", UniqueConstraint
    )
    assert "uq_outreach_target_campaign_influencer_channel" in constraint_names(
        "outreach_targets", UniqueConstraint
    )
    assert "uq_outreach_task_target_step_key" in constraint_names(
        "outreach_tasks", UniqueConstraint
    )
    assert "uq_message_template_version_template_version" in constraint_names(
        "message_template_versions", UniqueConstraint
    )
    assert "ck_outreach_task_version" in constraint_names("outreach_tasks", CheckConstraint)
    assert "ck_outreach_target_channel_reference_shape" in constraint_names(
        "outreach_targets", CheckConstraint
    )


def test_phase3a_idempotency_record_has_the_approved_persistence_shape() -> None:
    table = Base.metadata.tables["phase3a_idempotency_records"]

    assert set(table.columns.keys()) == {
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
    assert "updated_at" not in table.columns
    assert "fk_phase3a_idempotency_record_department" in constraint_names(
        "phase3a_idempotency_records", ForeignKeyConstraint
    )
    assert "uq_phase3a_idempotency_record_department_scope_key" in constraint_names(
        "phase3a_idempotency_records", UniqueConstraint
    )
    assert {
        "ck_phase3a_idempotency_record_key_length",
        "ck_phase3a_idempotency_record_request_hash",
        "ck_phase3a_idempotency_record_result_schema_version",
        "ck_phase3a_idempotency_record_payload_object",
        "ck_phase3a_idempotency_record_payload_size",
        "ck_phase3a_idempotency_record_bulk_add_payload",
    } <= constraint_names("phase3a_idempotency_records", CheckConstraint)
