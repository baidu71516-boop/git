"""Add the Phase 3A candidate, campaign, and outreach persistence foundation.

Revision ID: 0006_phase3a_persistence
Revises: 0005_phase2_refresh_queue
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_phase3a_persistence"
down_revision: str | None = "0005_phase2_refresh_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUMS: dict[str, tuple[str, ...]] = {
    "candidate_pool_kind": ("POTENTIAL_SELLER", "POTENTIAL_BUYER"),
    "candidate_pool_status": ("ACTIVE", "ARCHIVED"),
    "candidate_pool_run_status": ("PENDING", "RUNNING", "COMPLETED", "FAILED"),
    "candidate_result": ("MATCH", "UNKNOWN"),
    "campaign_status": ("DRAFT", "ACTIVE", "PAUSED", "CLOSED"),
    "campaign_review_mode": ("ALL", "FIRST_N", "SAMPLE", "AUTO"),
    "duplicate_history_policy": (
        "ALLOW_WITH_WARNING",
        "REQUIRE_CONFIRMATION",
        "BLOCK_WITHIN_WINDOW",
    ),
    "outreach_channel": (
        "EMAIL",
        "XIAOHONGSHU_PRIVATE_MESSAGE",
        "DOUYIN_PRIVATE_MESSAGE",
        "WECHAT",
        "MANUAL",
    ),
    "outreach_task_kind": ("FIRST_TOUCH", "FOLLOW_UP"),
    "outreach_task_state": ("REVIEW_REQUIRED", "READY", "SENT", "STOPPED", "FAILED"),
    "outreach_priority": ("NORMAL", "HIGH"),
    "outreach_priority_source": ("DEFAULT", "MANUAL", "POLICY"),
    "outreach_event_type": (
        "TASK_CREATED",
        "REVIEW_APPROVED",
        "OUTREACH_SENT",
        "OUTREACH_FAILED",
        "OUTREACH_STOPPED",
        "OUTREACH_RETRIED",
    ),
    "outreach_actor_type": ("OPERATOR", "SYSTEM"),
    "message_template_state": ("ACTIVE", "ARCHIVED"),
}


def timestamp_columns() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def enum_type(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(name=name, create_type=False)


def create_enums(bind: sa.Connection) -> None:
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)


def upgrade() -> None:
    bind = op.get_bind()
    create_enums(bind)
    jsonb = postgresql.JSONB(astext_type=sa.Text())

    # This redundant unique key is an additive seam for ownership-proving contact FKs.
    op.create_unique_constraint(
        "uq_influencer_contact_id_influencer", "influencer_contacts", ["id", "influencer_id"]
    )
    op.create_index(
        "ix_influencer_contacts_current_influencer_type",
        "influencer_contacts",
        ["influencer_id", "type"],
        postgresql_where=sa.text("is_current = true"),
    )
    op.create_index(
        "ix_influencer_contacts_current_influencer",
        "influencer_contacts",
        ["influencer_id"],
        postgresql_where=sa.text("is_current = true"),
    )
    op.create_index(
        "ix_platform_accounts_source_tags_gin",
        "influencer_platform_accounts",
        ["source_tags"],
        postgresql_using="gin",
    )
    for key in ("followers_count", "notes_7d", "notes_60d"):
        raw = f"metrics ->> '{key}'"
        normalized = f"NULLIF(ltrim({raw}, '0'), '')"
        expression = (
            "CASE WHEN "
            f"{raw} ~ '^[0-9]+$' AND "
            f"(length(COALESCE({normalized}, '0')) < 19 OR "
            f"(length(COALESCE({normalized}, '0')) = 19 AND "
            f"COALESCE({normalized}, '0') <= '9223372036854775807')) "
            f"THEN COALESCE({normalized}, '0')::bigint END"
        )
        op.create_index(
            f"ix_current_metrics_{key}_guarded",
            "influencer_current_metrics",
            [sa.text(f"({expression})")],
        )

    op.create_table(
        "candidate_pools",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("owner_operator_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", enum_type("candidate_pool_kind"), nullable=False),
        sa.Column("source_collection_job_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status", enum_type("candidate_pool_status"), nullable=False, server_default="ACTIVE"
        ),
        sa.Column("current_policy_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *timestamp_columns(),
        sa.CheckConstraint("version >= 1", name="ck_candidate_pool_version"),
        sa.ForeignKeyConstraint(
            ["owner_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_candidate_pool_owner_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_collection_job_id", "department_id"],
            ["collection_jobs.id", "collection_jobs.department_id"],
            name="fk_candidate_pool_source_collection_department",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "department_id", name="uq_candidate_pool_department_pair"),
    )
    op.create_index(
        "ix_candidate_pools_department_status_created",
        "candidate_pools",
        ["department_id", "status", "created_at", "id"],
    )

    op.create_table(
        "targeting_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pool_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("definition", jsonb, nullable=False),
        sa.Column("canonical_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_operator_id", sa.Uuid(), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("version >= 1", name="ck_targeting_policy_version"),
        sa.CheckConstraint("schema_version >= 1", name="ck_targeting_policy_schema_version"),
        sa.ForeignKeyConstraint(
            ["pool_id"],
            ["candidate_pools.id"],
            name="fk_targeting_policy_pool",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_operator_id"],
            ["operators.id"],
            name="fk_targeting_policy_creator",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pool_id", "version", name="uq_targeting_policy_pool_version"),
        sa.UniqueConstraint("id", "pool_id", name="uq_targeting_policy_id_pool"),
    )
    op.create_foreign_key(
        "fk_candidate_pool_current_policy",
        "candidate_pools",
        "targeting_policies",
        ["current_policy_id", "id"],
        ["id", "pool_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "candidate_pool_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pool_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_watermark", jsonb, nullable=True),
        sa.Column(
            "status",
            enum_type("candidate_pool_run_status"),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("match_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unknown_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("not_match_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint(
            "match_count >= 0 AND unknown_count >= 0 AND not_match_count >= 0",
            name="ck_candidate_pool_run_counts_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["pool_id"],
            ["candidate_pools.id"],
            name="fk_candidate_pool_run_pool",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id", "pool_id"],
            ["targeting_policies.id", "targeting_policies.pool_id"],
            name="fk_candidate_pool_run_policy_pool",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pool_id", "idempotency_key", name="uq_candidate_pool_run_pool_idempotency"
        ),
        sa.UniqueConstraint("id", "pool_id", name="uq_candidate_pool_run_id_pool"),
    )
    op.create_index(
        "ix_candidate_pool_runs_pool_created",
        "candidate_pool_runs",
        ["pool_id", "created_at", "id"],
    )

    op.create_table(
        "candidate_pool_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("influencer_id", sa.Uuid(), nullable=False),
        sa.Column("platform_account_id", sa.Uuid(), nullable=False),
        sa.Column("result", enum_type("candidate_result"), nullable=False),
        sa.Column("reason_codes", jsonb, nullable=False),
        sa.Column("redacted_evidence", jsonb, nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["candidate_pool_runs.id"],
            name="fk_candidate_pool_member_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["influencer_id"],
            ["influencers.id"],
            name="fk_candidate_pool_member_influencer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_candidate_pool_member_account_influencer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "platform_account_id", name="uq_candidate_pool_member_run_account"
        ),
    )
    op.create_index(
        "ix_candidate_pool_members_run_result", "candidate_pool_members", ["run_id", "result", "id"]
    )

    op.create_table(
        "campaigns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("owner_operator_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_operator_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", enum_type("campaign_status"), nullable=False, server_default="DRAFT"),
        sa.Column(
            "review_mode",
            enum_type("campaign_review_mode"),
            nullable=False,
            server_default="FIRST_N",
        ),
        sa.Column("review_count", sa.Integer(), nullable=True, server_default="50"),
        sa.Column(
            "duplicate_history_policy",
            enum_type("duplicate_history_policy"),
            nullable=False,
            server_default="ALLOW_WITH_WARNING",
        ),
        sa.Column("duplicate_window_days", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *timestamp_columns(),
        sa.CheckConstraint("version >= 1", name="ck_campaign_version"),
        sa.CheckConstraint(
            "review_count IS NULL OR review_count > 0", name="ck_campaign_review_count"
        ),
        sa.CheckConstraint(
            "(review_mode = 'FIRST_N' AND review_count IS NOT NULL) OR "
            "(review_mode != 'FIRST_N' AND review_count IS NULL)",
            name="ck_campaign_review_config",
        ),
        sa.CheckConstraint(
            "duplicate_window_days IS NULL OR duplicate_window_days > 0",
            name="ck_campaign_duplicate_window",
        ),
        sa.CheckConstraint(
            "(duplicate_history_policy = 'BLOCK_WITHIN_WINDOW' "
            "AND duplicate_window_days IS NOT NULL) OR "
            "(duplicate_history_policy != 'BLOCK_WITHIN_WINDOW')",
            name="ck_campaign_duplicate_policy_window",
        ),
        sa.ForeignKeyConstraint(
            ["owner_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_campaign_owner_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_campaign_creator_department",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "department_id", name="uq_campaign_department_pair"),
    )
    op.create_index(
        "ix_campaigns_department_status_updated",
        "campaigns",
        ["department_id", "status", "updated_at", "id"],
    )

    op.create_table(
        "campaign_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("influencer_id", sa.Uuid(), nullable=False),
        sa.Column("preferred_platform_account_id", sa.Uuid(), nullable=False),
        sa.Column("source_pool_run_id", sa.Uuid(), nullable=True),
        sa.Column("added_by_operator_id", sa.Uuid(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *timestamp_columns(),
        sa.CheckConstraint("version >= 1", name="ck_campaign_member_version"),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_campaign_member_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "department_id"],
            ["campaigns.id", "campaigns.department_id"],
            name="fk_campaign_member_campaign_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["influencer_id"],
            ["influencers.id"],
            name="fk_campaign_member_influencer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["preferred_platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_campaign_member_preferred_account_influencer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_pool_run_id"],
            ["candidate_pool_runs.id"],
            name="fk_campaign_member_source_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["added_by_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_campaign_member_adder_department",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "campaign_id", "influencer_id", name="uq_campaign_member_campaign_influencer"
        ),
        sa.UniqueConstraint(
            "id", "campaign_id", "influencer_id", name="uq_campaign_member_id_campaign_influencer"
        ),
    )
    op.create_index(
        "ix_campaign_members_campaign_removed",
        "campaign_members",
        ["campaign_id", "removed_at", "id"],
    )

    op.create_table(
        "outreach_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("influencer_id", sa.Uuid(), nullable=False),
        sa.Column("channel", enum_type("outreach_channel"), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=True),
        sa.Column("platform_account_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *timestamp_columns(),
        sa.CheckConstraint("version >= 1", name="ck_outreach_target_version"),
        sa.CheckConstraint(
            "(channel IN ('EMAIL', 'WECHAT') AND contact_id IS NOT NULL "
            "AND platform_account_id IS NULL) OR "
            "(channel IN ('XIAOHONGSHU_PRIVATE_MESSAGE', "
            "'DOUYIN_PRIVATE_MESSAGE') AND contact_id IS NULL "
            "AND platform_account_id IS NOT NULL) OR "
            "(channel = 'MANUAL' AND ((contact_id IS NOT NULL "
            "AND platform_account_id IS NULL) OR (contact_id IS NULL "
            "AND platform_account_id IS NOT NULL)))",
            name="ck_outreach_target_channel_reference_shape",
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_outreach_target_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "department_id"],
            ["campaigns.id", "campaigns.department_id"],
            name="fk_outreach_target_campaign_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["member_id", "campaign_id", "influencer_id"],
            [
                "campaign_members.id",
                "campaign_members.campaign_id",
                "campaign_members.influencer_id",
            ],
            name="fk_outreach_target_member_campaign_influencer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id", "influencer_id"],
            ["influencer_contacts.id", "influencer_contacts.influencer_id"],
            name="fk_outreach_target_contact_influencer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_outreach_target_account_influencer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "campaign_id",
            "influencer_id",
            "channel",
            name="uq_outreach_target_campaign_influencer_channel",
        ),
        sa.UniqueConstraint(
            "id", "campaign_id", "department_id", name="uq_outreach_target_id_campaign_department"
        ),
    )

    op.create_table(
        "message_templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_operator_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("channel", enum_type("outreach_channel"), nullable=False),
        sa.Column(
            "state", enum_type("message_template_state"), nullable=False, server_default="ACTIVE"
        ),
        sa.Column("metadata", jsonb, nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_message_template_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_message_template_creator_department",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "department_id", name="uq_message_template_department_pair"),
        sa.UniqueConstraint(
            "department_id", "name", "channel", name="uq_message_template_department_name_channel"
        ),
    )
    op.create_table(
        "message_template_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("variable_schema", jsonb, nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("created_by_operator_id", sa.Uuid(), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("version >= 1", name="ck_message_template_version_version"),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["message_templates.id"],
            name="fk_message_template_version_template",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_operator_id"],
            ["operators.id"],
            name="fk_message_template_version_creator",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "template_id", "version", name="uq_message_template_version_template_version"
        ),
    )

    op.create_table(
        "outreach_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("outreach_target_id", sa.Uuid(), nullable=False),
        sa.Column("kind", enum_type("outreach_task_kind"), nullable=False),
        sa.Column("step_key", sa.String(length=120), nullable=False),
        sa.Column("state", enum_type("outreach_task_state"), nullable=False),
        sa.Column(
            "priority", enum_type("outreach_priority"), nullable=False, server_default="NORMAL"
        ),
        sa.Column(
            "priority_source",
            enum_type("outreach_priority_source"),
            nullable=False,
            server_default="DEFAULT",
        ),
        sa.Column("priority_reason_codes", jsonb, nullable=True),
        sa.Column("assigned_operator_id", sa.Uuid(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("create_idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("create_request_hash", sa.String(length=64), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("version >= 1", name="ck_outreach_task_version"),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_outreach_task_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["outreach_target_id", "campaign_id", "department_id"],
            [
                "outreach_targets.id",
                "outreach_targets.campaign_id",
                "outreach_targets.department_id",
            ],
            name="fk_outreach_task_target_campaign_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_outreach_task_assignee_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["message_template_versions.id"],
            name="fk_outreach_task_template_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "outreach_target_id", "step_key", name="uq_outreach_task_target_step_key"
        ),
        sa.UniqueConstraint(
            "department_id",
            "create_idempotency_key",
            name="uq_outreach_task_department_create_idempotency",
        ),
    )
    op.create_index(
        "ix_outreach_tasks_today_queue",
        "outreach_tasks",
        ["department_id", "state", "priority", "due_at", "id"],
    )
    op.create_index(
        "ix_outreach_tasks_owner_queue",
        "outreach_tasks",
        ["department_id", "assigned_operator_id", "state", "due_at", "id"],
    )
    op.create_index(
        "ix_outreach_tasks_campaign_queue",
        "outreach_tasks",
        ["campaign_id", "state", "due_at", "id"],
    )

    op.create_table(
        "outreach_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("influencer_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("channel", enum_type("outreach_channel"), nullable=False),
        sa.Column("event_type", enum_type("outreach_event_type"), nullable=False),
        sa.Column("actor_type", enum_type("outreach_actor_type"), nullable=False),
        sa.Column("actor_operator_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_state", enum_type("outreach_task_state"), nullable=True),
        sa.Column("to_state", enum_type("outreach_task_state"), nullable=True),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("redacted_target_snapshot", jsonb, nullable=True),
        sa.Column("redacted_message_snapshot", jsonb, nullable=True),
        sa.Column("metadata", jsonb, nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_outreach_event_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "department_id"],
            ["campaigns.id", "campaigns.department_id"],
            name="fk_outreach_event_campaign_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["influencer_id"],
            ["influencers.id"],
            name="fk_outreach_event_influencer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["outreach_tasks.id"], name="fk_outreach_event_task", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["actor_operator_id"],
            ["operators.id"],
            name="fk_outreach_event_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "task_id", "idempotency_key", name="uq_outreach_event_task_idempotency"
        ),
    )
    op.create_index(
        "ix_outreach_events_history",
        "outreach_events",
        [
            "department_id",
            "influencer_id",
            "channel",
            sa.text("occurred_at DESC"),
            sa.text("id DESC"),
        ],
    )
    op.create_index(
        "ix_outreach_events_task_history", "outreach_events", ["task_id", "occurred_at", "id"]
    )
    op.execute(
        """
        CREATE FUNCTION prevent_outreach_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'outreach_events is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_outreach_events_append_only BEFORE UPDATE OR DELETE "
        "ON outreach_events FOR EACH ROW EXECUTE FUNCTION prevent_outreach_event_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_outreach_events_append_only ON outreach_events")
    op.execute("DROP FUNCTION prevent_outreach_event_mutation()")
    op.drop_table("outreach_events")
    op.drop_table("outreach_tasks")
    op.drop_table("message_template_versions")
    op.drop_table("message_templates")
    op.drop_table("outreach_targets")
    op.drop_table("campaign_members")
    op.drop_table("campaigns")
    op.drop_table("candidate_pool_members")
    op.drop_table("candidate_pool_runs")
    op.drop_constraint("fk_candidate_pool_current_policy", "candidate_pools", type_="foreignkey")
    op.drop_table("targeting_policies")
    op.drop_table("candidate_pools")
    op.drop_index("ix_current_metrics_notes_60d_guarded", table_name="influencer_current_metrics")
    op.drop_index("ix_current_metrics_notes_7d_guarded", table_name="influencer_current_metrics")
    op.drop_index(
        "ix_current_metrics_followers_count_guarded", table_name="influencer_current_metrics"
    )
    op.drop_index("ix_platform_accounts_source_tags_gin", table_name="influencer_platform_accounts")
    op.drop_index("ix_influencer_contacts_current_influencer", table_name="influencer_contacts")
    op.drop_index(
        "ix_influencer_contacts_current_influencer_type", table_name="influencer_contacts"
    )
    op.drop_constraint("uq_influencer_contact_id_influencer", "influencer_contacts", type_="unique")
    bind = op.get_bind()
    for name in reversed(ENUMS):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
