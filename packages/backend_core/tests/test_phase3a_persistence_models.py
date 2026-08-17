"""Phase 3A persistence metadata contract tests."""

import backend_core.db.models  # noqa: F401
from backend_core.db import Base
from backend_core.growth.enums import CampaignStatus, CandidatePoolKind, CandidateResult
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
}


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
