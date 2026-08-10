"""Create Phase 1B collection, import, and platform-neutral influencer tables.

Revision ID: 0003_phase1b
Revises: 0002_phase1a
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_phase1b"
down_revision: str | None = "0002_phase1a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

stored_file_type = postgresql.ENUM("csv", "xlsx", name="stored_file_type")
collection_job_status = postgresql.ENUM(
    "draft", "active", "completed", "cancelled", name="collection_job_status"
)
import_source_type = postgresql.ENUM(
    "manual_huitun_export", "generic_csv", name="import_source_type"
)
import_job_status = postgresql.ENUM(
    "uploaded",
    "parsing",
    "mapping_required",
    "previewing",
    "preview_ready",
    "preview_stale",
    "confirm_queued",
    "importing",
    "completed",
    "failed",
    "cancelled",
    name="import_job_status",
)
platform_enum = postgresql.ENUM("xiaohongshu", name="platform_enum")
data_source = postgresql.ENUM("huitun", "generic", "manual", name="data_source")
crm_stage = postgresql.ENUM(
    "待开发",
    "已发送邮件",
    "第一次跟进",
    "第二次跟进",
    "已回复",
    "已加微信",
    "沟通中",
    "潜在合作",
    "高意向",
    "暂不考虑",
    "长期维护",
    "已结束",
    name="crm_stage",
)
influencer_status = postgresql.ENUM("active", "disabled", name="influencer_status")
import_match_type = postgresql.ENUM(
    "platform_account_id",
    "external_source_id",
    "normalized_profile_url",
    "none",
    name="import_match_type",
)
import_row_action = postgresql.ENUM(
    "create",
    "update",
    "no_change",
    "skip",
    "error",
    "manual_review",
    name="import_row_action",
)
contact_type = postgresql.ENUM("email", "wechat", "phone", "other", name="contact_type")
contact_validation_status = postgresql.ENUM(
    "valid", "invalid", "unverified", name="contact_validation_status"
)

AUDIT_ACTIONS = (
    "IMPORT_FILE_UPLOADED",
    "IMPORT_MAPPING_UPDATED",
    "IMPORT_PREVIEW_CREATED",
    "IMPORT_PREVIEW_REGENERATED",
    "IMPORT_CONFIRM_REQUESTED",
    "IMPORT_CONFIRMED",
    "IMPORT_PREVIEW_STALE",
    "IMPORT_COMPLETED",
    "IMPORT_FAILED",
    "IMPORT_CANCELLED",
)


def timestamp_columns() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
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


def enum_column(name: str, enum_name: str, *, nullable: bool = False) -> sa.Column[sa.Enum]:
    return sa.Column(
        name,
        postgresql.ENUM(name=enum_name, create_type=False),
        nullable=nullable,
    )


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (
        stored_file_type,
        collection_job_status,
        import_source_type,
        import_job_status,
        platform_enum,
        data_source,
        crm_stage,
        influencer_status,
        import_match_type,
        import_row_action,
        contact_type,
        contact_validation_status,
    ):
        enum_type.create(bind, checkfirst=True)

    # PostgreSQL cannot reliably use a freshly added enum value in the same
    # transaction on all supported versions. No Phase 1B table creation relies
    # on these values, so add them in explicit autocommit blocks.
    with op.get_context().autocommit_block():
        for action in AUDIT_ACTIONS:
            op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{action}'")

    op.create_unique_constraint("uq_operators_id_department", "operators", ["id", "department_id"])

    op.create_table(
        "stored_import_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        enum_column("detected_type", "stored_file_type"),
        sa.Column("detected_mime", sa.String(length=160), nullable=False),
        sa.Column("encoding", sa.String(length=40), nullable=True),
        sa.Column("parse_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("size > 0", name="ck_stored_import_file_size"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_stored_import_files_expires_at", "stored_import_files", ["expires_at"])

    op.create_table(
        "collection_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("industry", sa.String(length=160), nullable=False),
        sa.Column("subdirection", sa.String(length=200), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("target_action", sa.String(length=160), nullable=False),
        sa.Column("follower_min", sa.Integer(), nullable=True),
        sa.Column("follower_max", sa.Integer(), nullable=True),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("owner_operator_id", sa.Uuid(), nullable=False),
        enum_column("source_type", "import_source_type"),
        sa.Column(
            "status",
            postgresql.ENUM(name="collection_job_status", create_type=False),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        *timestamp_columns(),
        sa.CheckConstraint("target_count > 0", name="ck_collection_job_target_count"),
        sa.CheckConstraint(
            "follower_min IS NULL OR follower_min >= 0",
            name="ck_collection_job_follower_min",
        ),
        sa.CheckConstraint(
            "follower_max IS NULL OR follower_max >= 0",
            name="ck_collection_job_follower_max",
        ),
        sa.CheckConstraint(
            "follower_min IS NULL OR follower_max IS NULL OR follower_min <= follower_max",
            name="ck_collection_job_follower_range",
        ),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["owner_operator_id", "department_id"],
            ["operators.id", "operators.department_id"],
            name="fk_collection_job_owner_department",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "department_id", name="uq_collection_job_department_pair"),
    )
    op.create_index(
        "ix_collection_jobs_department_status",
        "collection_jobs",
        ["department_id", "status"],
    )

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("collection_job_id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("stored_file_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=160), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        enum_column("source_type", "import_source_type"),
        enum_column("status", "import_job_status"),
        sa.Column("detected_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("field_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("mapping_hash", sa.String(length=64), nullable=True),
        sa.Column("preview_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("preview_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("no_change_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manual_review_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed_revision", sa.Integer(), nullable=True),
        sa.Column("parse_task_id", sa.String(length=64), nullable=True),
        sa.Column("confirm_task_id", sa.String(length=64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        *timestamp_columns(),
        sa.CheckConstraint("file_size > 0", name="ck_import_job_file_size"),
        sa.CheckConstraint("preview_revision >= 0", name="ck_import_job_preview_revision"),
        sa.CheckConstraint(
            "confirmed_revision IS NULL OR confirmed_revision <= preview_revision",
            name="ck_import_job_confirmed_revision",
        ),
        sa.CheckConstraint(
            "total_rows >= 0 AND valid_rows >= 0 AND warning_rows >= 0 "
            "AND error_rows >= 0 AND created_rows >= 0 AND updated_rows >= 0 "
            "AND no_change_rows >= 0 AND skipped_rows >= 0 AND manual_review_rows >= 0",
            name="ck_import_job_counts_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["collection_job_id", "department_id"],
            ["collection_jobs.id", "collection_jobs.department_id"],
            name="fk_import_job_collection_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operator_id"], ["operators.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["stored_file_id"], ["stored_import_files.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_jobs_department_status", "import_jobs", ["department_id", "status"])
    op.create_index("ix_import_jobs_sha256", "import_jobs", ["sha256"])
    op.create_index("ix_import_jobs_collection", "import_jobs", ["collection_job_id"])
    op.create_index("ix_import_jobs_stored_file", "import_jobs", ["stored_file_id"])

    op.create_table(
        "influencers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("owner_operator_id", sa.Uuid(), nullable=True),
        sa.Column(
            "crm_stage",
            postgresql.ENUM(name="crm_stage", create_type=False),
            nullable=False,
            server_default="待开发",
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="influencer_status", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["owner_operator_id"], ["operators.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "influencer_platform_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("influencer_id", sa.Uuid(), nullable=False),
        enum_column("platform", "platform_enum"),
        sa.Column("platform_account_id", sa.String(length=160), nullable=True),
        sa.Column("account_name", sa.String(length=160), nullable=False),
        sa.Column("account_handle", sa.String(length=160), nullable=True),
        sa.Column("profile_url", sa.Text(), nullable=True),
        sa.Column("normalized_profile_url", sa.String(length=1024), nullable=True),
        enum_column("source", "data_source"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("gender", sa.String(length=40), nullable=True),
        sa.Column("region_raw", sa.Text(), nullable=True),
        sa.Column("verification_info", sa.Text(), nullable=True),
        sa.Column("mcn_name", sa.String(length=200), nullable=True),
        sa.Column("source_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("creator_level", sa.String(length=120), nullable=True),
        sa.Column("is_brand_partner", sa.Boolean(), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["influencer_id"], ["influencers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "influencer_id", name="uq_platform_account_influencer_pair"),
        sa.UniqueConstraint("id", "platform", name="uq_platform_account_platform_pair"),
        sa.UniqueConstraint("platform", "platform_account_id", name="uq_platform_account_identity"),
        sa.UniqueConstraint("platform", "normalized_profile_url", name="uq_platform_profile_url"),
    )
    op.create_index(
        "ix_platform_accounts_influencer",
        "influencer_platform_accounts",
        ["influencer_id", "is_active"],
    )

    op.create_table(
        "import_rows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("import_job_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("normalized_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("matched_influencer_id", sa.Uuid(), nullable=True),
        sa.Column("matched_platform_account_id", sa.Uuid(), nullable=True),
        sa.Column(
            "match_type",
            postgresql.ENUM(name="import_match_type", create_type=False),
            nullable=False,
            server_default="none",
        ),
        enum_column("action", "import_row_action"),
        sa.Column("merge_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("preview_revision", sa.Integer(), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "committed_action",
            postgresql.ENUM(name="import_row_action", create_type=False),
            nullable=True,
        ),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.CheckConstraint("row_number >= 2", name="ck_import_row_number"),
        sa.CheckConstraint(
            "matched_platform_account_id IS NULL OR matched_influencer_id IS NOT NULL",
            name="ck_import_row_match_pair",
        ),
        sa.ForeignKeyConstraint(["import_job_id"], ["import_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["matched_influencer_id"], ["influencers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["matched_platform_account_id", "matched_influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_import_row_match_account_influencer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "import_job_id", name="uq_import_row_job_pair"),
        sa.UniqueConstraint("import_job_id", "row_number", name="uq_import_rows_job_number"),
    )
    op.create_index("ix_import_rows_job_action", "import_rows", ["import_job_id", "action"])

    op.create_table(
        "influencer_source_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("influencer_id", sa.Uuid(), nullable=False),
        sa.Column("platform_account_id", sa.Uuid(), nullable=False),
        enum_column("source", "data_source"),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_data_hash", sa.String(length=64), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_import_job_id", sa.Uuid(), nullable=False),
        sa.Column("last_import_row_id", sa.Uuid(), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("state_version >= 1", name="ck_source_state_version"),
        sa.ForeignKeyConstraint(["influencer_id"], ["influencers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_source_state_account_influencer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_import_row_id", "last_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_source_state_last_import",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform_account_id", "source", name="uq_platform_account_source_state"
        ),
    )

    op.create_table(
        "influencer_contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("influencer_id", sa.Uuid(), nullable=False),
        sa.Column("platform_account_id", sa.Uuid(), nullable=True),
        enum_column("type", "contact_type"),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.String(length=512), nullable=False),
        enum_column("source", "data_source"),
        enum_column("validation_status", "contact_validation_status"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "possible_duplicate_contact", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_import_job_id", sa.Uuid(), nullable=True),
        sa.Column("first_import_row_id", sa.Uuid(), nullable=True),
        sa.Column("last_import_job_id", sa.Uuid(), nullable=True),
        sa.Column("last_import_row_id", sa.Uuid(), nullable=True),
        *timestamp_columns(),
        sa.CheckConstraint(
            "(first_import_job_id IS NULL AND first_import_row_id IS NULL) OR "
            "(first_import_job_id IS NOT NULL AND first_import_row_id IS NOT NULL)",
            name="ck_contact_first_import_pair",
        ),
        sa.CheckConstraint(
            "(last_import_job_id IS NULL AND last_import_row_id IS NULL) OR "
            "(last_import_job_id IS NOT NULL AND last_import_row_id IS NOT NULL)",
            name="ck_contact_last_import_pair",
        ),
        sa.CheckConstraint(
            "source = 'manual' OR (first_import_job_id IS NOT NULL AND "
            "last_import_job_id IS NOT NULL)",
            name="ck_contact_import_provenance",
        ),
        sa.CheckConstraint("last_seen_at >= first_seen_at", name="ck_contact_seen_range"),
        sa.ForeignKeyConstraint(["influencer_id"], ["influencers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_contact_account_influencer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["first_import_row_id", "first_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_contact_first_import",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_import_row_id", "last_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_contact_last_import",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "influencer_id",
            "type",
            "normalized_value",
            "source",
            name="uq_influencer_contact_source",
        ),
    )
    op.create_index(
        "ix_influencer_contacts_normalized",
        "influencer_contacts",
        ["type", "normalized_value"],
    )

    op.create_table(
        "influencer_current_metrics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("influencer_id", sa.Uuid(), nullable=False),
        sa.Column("platform_account_id", sa.Uuid(), nullable=False),
        enum_column("source", "data_source"),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metrics_hash", sa.String(length=64), nullable=False),
        sa.Column("last_import_job_id", sa.Uuid(), nullable=False),
        sa.Column("last_import_row_id", sa.Uuid(), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["influencer_id"], ["influencers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_current_metrics_account_influencer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_import_row_id", "last_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_current_metrics_last_import",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform_account_id", "source", name="uq_platform_account_current_metrics"
        ),
    )

    op.create_table(
        "influencer_metric_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("influencer_id", sa.Uuid(), nullable=False),
        sa.Column("platform_account_id", sa.Uuid(), nullable=False),
        enum_column("source", "data_source"),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("import_job_id", sa.Uuid(), nullable=False),
        sa.Column("import_row_id", sa.Uuid(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metrics_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_key", sa.String(length=64), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["influencer_id"], ["influencers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_metric_snapshot_account_influencer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["import_row_id", "import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_metric_snapshot_import",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_row_id", name="uq_metric_snapshot_import_row"),
        sa.UniqueConstraint("snapshot_key", name="uq_metric_snapshot_key"),
        sa.UniqueConstraint(
            "platform_account_id",
            "source",
            "source_updated_at",
            "metrics_hash",
            name="uq_platform_metric_snapshot",
        ),
    )
    op.create_index(
        "ix_metric_snapshots_influencer_captured",
        "influencer_metric_snapshots",
        ["influencer_id", "captured_at"],
    )

    op.create_table(
        "platform_account_source_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("platform_account_id", sa.Uuid(), nullable=False),
        enum_column("platform", "platform_enum"),
        enum_column("source", "data_source"),
        sa.Column("external_account_id", sa.String(length=160), nullable=False),
        sa.Column("first_import_job_id", sa.Uuid(), nullable=False),
        sa.Column("first_import_row_id", sa.Uuid(), nullable=False),
        sa.Column("last_import_job_id", sa.Uuid(), nullable=False),
        sa.Column("last_import_row_id", sa.Uuid(), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["platform_account_id", "platform"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.platform"],
            name="fk_source_identity_account_platform",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["first_import_row_id", "first_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_source_identity_first_import",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_import_row_id", "last_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_source_identity_last_import",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform_account_id",
            "source",
            "external_account_id",
            name="uq_account_source_external_account",
        ),
        sa.UniqueConstraint(
            "source",
            "platform",
            "external_account_id",
            name="uq_source_platform_external_account",
        ),
    )
    op.create_index(
        "ix_source_identity_account",
        "platform_account_source_identities",
        ["platform_account_id", "source"],
    )


def downgrade() -> None:
    op.drop_table("platform_account_source_identities")
    op.drop_table("influencer_metric_snapshots")
    op.drop_table("influencer_current_metrics")
    op.drop_table("influencer_contacts")
    op.drop_table("influencer_source_states")
    op.drop_table("import_rows")
    op.drop_table("influencer_platform_accounts")
    op.drop_table("influencers")
    op.drop_table("import_jobs")
    op.drop_table("collection_jobs")
    op.drop_table("stored_import_files")
    op.drop_constraint("uq_operators_id_department", "operators", type_="unique")

    bind = op.get_bind()
    for enum_type in (
        contact_validation_status,
        contact_type,
        import_row_action,
        import_match_type,
        influencer_status,
        crm_stage,
        data_source,
        platform_enum,
        import_job_status,
        import_source_type,
        collection_job_status,
        stored_file_type,
    ):
        enum_type.drop(bind, checkfirst=True)

    # Audit enum additions are intentionally retained. PostgreSQL cannot remove
    # enum values without rebuilding the shared type and risking historical logs.
