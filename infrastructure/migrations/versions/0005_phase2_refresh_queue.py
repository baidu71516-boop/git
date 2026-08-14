"""Add the Phase 2 department-owned refresh queue aggregate.

Revision ID: 0005_phase2_refresh_queue
Revises: 0004_phase2_bulk_import
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_phase2_refresh_queue"
down_revision: str | None = "0004_phase2_bulk_import"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

refresh_queue_status = postgresql.ENUM(
    "open",
    "exported",
    "completed",
    "cancelled",
    name="refresh_queue_status",
)
refresh_queue_item_status = postgresql.ENUM(
    "pending",
    "fulfilled_changed",
    "fulfilled_no_change",
    "stale_return",
    "unresolved",
    "cancelled",
    name="refresh_queue_item_status",
)

AUDIT_ACTIONS = (
    "REFRESH_QUEUE_CREATED",
    "REFRESH_QUEUE_EXPORTED",
    "REFRESH_QUEUE_CANCELLED",
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


def _preflight_downgrade() -> None:
    unsafe = bool(
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT
                    EXISTS (SELECT 1 FROM refresh_queues)
                    OR EXISTS (SELECT 1 FROM refresh_queue_items)
                    OR EXISTS (
                        SELECT 1
                        FROM import_jobs
                        WHERE refresh_queue_id IS NOT NULL
                    )
                """
            )
        )
        .scalar_one()
    )
    if unsafe:
        raise RuntimeError(
            "0005 downgrade blocked: refresh queues, items, or import queue references exist"
        )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for action in AUDIT_ACTIONS:
            op.execute(f"ALTER TYPE audit_action ADD VALUE IF NOT EXISTS '{action}'")

    bind = op.get_bind()
    refresh_queue_status.create(bind, checkfirst=True)
    refresh_queue_item_status.create(bind, checkfirst=True)

    op.create_table(
        "refresh_queues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_operator_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="refresh_queue_status", create_type=False),
            nullable=False,
            server_default="open",
        ),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_limit", sa.Integer(), nullable=False),
        sa.Column("today_total_limit", sa.Integer(), nullable=False),
        sa.Column("refresh_limit", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column(
            "criteria_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.CheckConstraint(
            "requested_limit > 0 AND today_total_limit > 0 AND refresh_limit > 0",
            name="ck_refresh_queue_limits_positive",
        ),
        sa.CheckConstraint(
            "requested_limit <= refresh_limit AND refresh_limit <= today_total_limit",
            name="ck_refresh_queue_limit_order",
        ),
        sa.CheckConstraint(
            "requested_limit <= 2000",
            name="ck_refresh_queue_requested_limit_max",
        ),
        sa.CheckConstraint("policy_version >= 1", name="ck_refresh_queue_policy_version"),
        sa.CheckConstraint(
            "(status = 'open' AND exported_at IS NULL "
            "AND completed_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'exported' AND exported_at IS NOT NULL "
            "AND completed_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' AND completed_at IS NULL AND cancelled_at IS NOT NULL)",
            name="ck_refresh_queue_status_timestamps",
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_operator_id"],
            ["operators.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "department_id", name="uq_refresh_queue_department_pair"),
    )
    op.create_index(
        "ix_refresh_queues_department_status_created",
        "refresh_queues",
        ["department_id", "status", "created_at", "id"],
    )

    op.create_table(
        "refresh_queue_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("queue_id", sa.Uuid(), nullable=False),
        sa.Column("influencer_id", sa.Uuid(), nullable=False),
        sa.Column("platform_account_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(name="data_source", create_type=False),
            nullable=False,
        ),
        sa.Column("priority_tier", sa.Integer(), nullable=False),
        sa.Column(
            "priority_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "identity_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("baseline_last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline_source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="refresh_queue_item_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("fulfilled_import_job_id", sa.Uuid(), nullable=True),
        sa.Column("fulfilled_import_row_id", sa.Uuid(), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_return_import_job_id", sa.Uuid(), nullable=True),
        sa.Column("last_return_import_row_id", sa.Uuid(), nullable=True),
        *timestamp_columns(),
        sa.CheckConstraint(
            "priority_tier >= 1 AND priority_tier <= 5",
            name="ck_refresh_queue_item_priority_tier",
        ),
        sa.CheckConstraint(
            "source = 'huitun'",
            name="ck_refresh_queue_item_source_huitun",
        ),
        sa.CheckConstraint(
            "(fulfilled_import_job_id IS NULL AND fulfilled_import_row_id IS NULL) OR "
            "(fulfilled_import_job_id IS NOT NULL AND fulfilled_import_row_id IS NOT NULL)",
            name="ck_refresh_queue_item_fulfillment_pair",
        ),
        sa.CheckConstraint(
            "(last_return_import_job_id IS NULL AND last_return_import_row_id IS NULL) OR "
            "(last_return_import_job_id IS NOT NULL AND last_return_import_row_id IS NOT NULL)",
            name="ck_refresh_queue_item_last_return_pair",
        ),
        sa.CheckConstraint(
            "(status IN ('fulfilled_changed', 'fulfilled_no_change') "
            "AND fulfilled_import_job_id IS NOT NULL "
            "AND fulfilled_import_row_id IS NOT NULL AND fulfilled_at IS NOT NULL) OR "
            "(status NOT IN ('fulfilled_changed', 'fulfilled_no_change') "
            "AND fulfilled_import_job_id IS NULL "
            "AND fulfilled_import_row_id IS NULL AND fulfilled_at IS NULL)",
            name="ck_refresh_queue_item_fulfillment_state",
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["queue_id", "department_id"],
            ["refresh_queues.id", "refresh_queues.department_id"],
            name="fk_refresh_queue_item_queue_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["influencer_id"],
            ["influencers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["platform_account_id", "influencer_id"],
            ["influencer_platform_accounts.id", "influencer_platform_accounts.influencer_id"],
            name="fk_refresh_queue_item_account_influencer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fulfilled_import_row_id", "fulfilled_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_refresh_queue_item_fulfilled_import",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_return_import_row_id", "last_return_import_job_id"],
            ["import_rows.id", "import_rows.import_job_id"],
            name="fk_refresh_queue_item_last_return_import",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "queue_id",
            "platform_account_id",
            "source",
            name="uq_refresh_queue_item_queue_account_source",
        ),
    )
    op.create_index(
        "uq_refresh_queue_item_active_candidate",
        "refresh_queue_items",
        ["department_id", "platform_account_id", "source"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'stale_return', 'unresolved')"),
    )
    op.create_index(
        "ix_refresh_queue_items_queue_status",
        "refresh_queue_items",
        ["queue_id", "status"],
    )
    op.create_index(
        "ix_refresh_queue_items_queue_priority",
        "refresh_queue_items",
        [
            "queue_id",
            "priority_tier",
            "baseline_last_observed_at",
            "influencer_id",
            "platform_account_id",
            "id",
        ],
    )

    op.add_column("import_jobs", sa.Column("refresh_queue_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_import_job_refresh_queue_department",
        "import_jobs",
        "refresh_queues",
        ["refresh_queue_id", "department_id"],
        ["id", "department_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE import_jobs, refresh_queue_items, refresh_queues IN ACCESS EXCLUSIVE MODE"
    )
    _preflight_downgrade()

    op.drop_constraint(
        "fk_import_job_refresh_queue_department",
        "import_jobs",
        type_="foreignkey",
    )
    op.drop_column("import_jobs", "refresh_queue_id")

    op.drop_index(
        "ix_refresh_queue_items_queue_priority",
        table_name="refresh_queue_items",
    )
    op.drop_index(
        "ix_refresh_queue_items_queue_status",
        table_name="refresh_queue_items",
    )
    op.drop_index(
        "uq_refresh_queue_item_active_candidate",
        table_name="refresh_queue_items",
    )
    op.drop_table("refresh_queue_items")
    op.drop_index(
        "ix_refresh_queues_department_status_created",
        table_name="refresh_queues",
    )
    op.drop_table("refresh_queues")

    bind = op.get_bind()
    refresh_queue_item_status.drop(bind, checkfirst=True)
    refresh_queue_status.drop(bind, checkfirst=True)

    # Audit enum additions are retained: removing PostgreSQL enum values would
    # require rebuilding a shared type and could invalidate historical logs.
